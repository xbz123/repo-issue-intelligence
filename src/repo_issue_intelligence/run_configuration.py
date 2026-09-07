"""Capture requested Protocol v2 configuration and runtime provenance.

The functions here record what the client requested and what the local
process can actually observe.  They never claim a provider-side effective
configuration and never serialize credentials.  Capture is explicit; importing
this module does not alter the legacy V1 run path.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import math
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

from .protocol_v2_models import (
    BudgetConfiguration,
    ClientConfiguration,
    EngineRuntime,
    ParameterOrigin,
    ProtocolConfiguration,
    RunConfiguration,
    freeze_mapping,
)


class RunConfigurationError(ValueError):
    """Fail-closed requested configuration error."""

    def __init__(self, message: str, *, code: str = "run_configuration_invalid") -> None:
        self.code = code
        super().__init__(message)


class _Omitted:
    pass


_OMITTED = _Omitted()
_SECRET_NAME = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|secret|token)"
    r"(?:$|[_=:-])",
    re.I,
)
_SAFE_PARAMETER_NAMES = frozenset(
    {
        "reasoning_effort",
        "service_tier",
        "temperature",
        "seed",
        "max_output_tokens",
        "timeout_seconds",
        "response_format_json",
        "max_evidence_chars",
        "max_lines_per_evidence",
        "max_evidence_lines",
        "retry_policy",
        "output_tokens",
        "evidence_chars",
        "evidence_lines",
    }
)
_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "reasoning_effort": ("reasoning_effort", "llm_reasoning_effort", "codex_cli_reasoning_effort"),
    "service_tier": ("service_tier", "llm_service_tier"),
    "temperature": ("temperature", "llm_temperature", "opencode_temperature"),
    "seed": ("seed", "llm_seed"),
    "max_output_tokens": (
        "max_output_tokens",
        "llm_max_output_tokens",
        "opencode_max_output_tokens",
    ),
    "timeout_seconds": (
        "timeout_seconds",
        "llm_timeout_seconds",
        "opencode_timeout_seconds",
        "codex_cli_timeout_seconds",
    ),
    "response_format_json": ("response_format_json", "llm_response_format_json"),
}
_MODEL_ALIASES = ("requested_model", "model", "llm_model", "codex_cli_model", "opencode_model")
_BACKEND_ALIASES = ("backend", "llm_backend")
_PROVIDER_ALIASES = ("provider", "llm_api_provider", "opencode_provider")
_ENDPOINT_ALIASES = ("endpoint", "base_url", "llm_api_base_url", "opencode_api_base_url")
_BUDGET_ALIASES: dict[str, tuple[str, ...]] = {
    "output_tokens": (
        "output_tokens",
        "max_output_tokens",
        "llm_max_output_tokens",
        "opencode_max_output_tokens",
    ),
    "evidence_chars": ("evidence_chars", "max_evidence_chars", "llm_max_evidence_chars"),
    "evidence_lines": (
        "evidence_lines",
        "max_evidence_lines",
        "llm_max_lines_per_evidence",
    ),
    "timeout_seconds": (
        "timeout_seconds",
        "llm_timeout_seconds",
        "codex_cli_timeout_seconds",
    ),
    "retry_policy": ("retry_policy", "llm_retry_policy"),
}
_RETRY_POLICY_CATEGORIES = frozenset(
    {
        "transport",
        "rate_limit",
        "server_error",
        "timeout",
    }
)
_RETRY_POLICY_FIELDS = frozenset({"backoff", "categories", "max_attempts"})
_SOURCE_FIELD_NAMES = frozenset(
    {
        *_MODEL_ALIASES,
        *_BACKEND_ALIASES,
        *_PROVIDER_ALIASES,
        *_ENDPOINT_ALIASES,
        *(
            alias
            for aliases in _PARAMETER_ALIASES.values()
            for alias in aliases
        ),
        *(alias for aliases in _BUDGET_ALIASES.values() for alias in aliases),
        "requested",
        "request_parameters",
        "safe_cli_flags",
        "cli_flags",
        "cli_executable",
        "codex_cli_executable",
        "cli_version",
        "codex_cli_version",
        "provider_schema_version",
        "provider_schema",
        "prompt_version",
        "analysis_protocol_version",
        "analysis_version",
        "retrieval_protocol_version",
        "retrieval_version",
        "index_version",
        "top_k",
        "selection_protocol",
    }
)


class ConfigurationValueSource(StrEnum):
    USER_CONFIG = ParameterOrigin.USER_CONFIG.value
    CLIENT_DEFAULT = ParameterOrigin.CLIENT_DEFAULT.value
    OMITTED = ParameterOrigin.OMITTED.value


_MAX_URL_PERCENT_DECODE_ROUNDS = 8


def _fully_percent_decode(value: str) -> str | None:
    """Decode URL text for validation without changing the retained spelling.

    ``urlsplit`` intentionally does not validate or percent-decode URL text.
    We therefore inspect a bounded number of decoded representations for
    delimiter smuggling, while callers continue to retain the original URL
    spelling.  Excessively nested encodings fail closed.
    """

    current = value
    for _ in range(_MAX_URL_PERCENT_DECODE_ROUNDS):
        decoded = unquote(current)
        if decoded == current:
            return current
        current = decoded
    return current if unquote(current) == current else None


def _url_security_violation(value: str) -> str | None:
    """Return a generic URL safety violation without exposing URL contents."""

    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
    except ValueError:
        return "invalid"

    # Check every URL scheme, not just HTTP(S): CLI flags can carry SSH/FTP
    # endpoints too.  A percent-encoded ``@`` is treated as userinfo as well.
    decoded_netloc = _fully_percent_decode(parsed.netloc)
    if decoded_netloc is None:
        return "encoded"
    if username is not None or password is not None or "@" in decoded_netloc:
        return "userinfo"

    # ``urlsplit`` treats ``user:password@example.com/repo`` as a custom
    # scheme named ``user`` rather than as an authority.  It is still a
    # URL-shaped credential-bearing value, so inspect the fully decoded text
    # when the authority-like suffix has a path.  Requiring the path keeps
    # ordinary label/email text outside this URL-specific check.
    decoded_value = _fully_percent_decode(value)
    if decoded_value is None:
        return "encoded"
    at_index = decoded_value.find("@")
    if at_index > 0:
        userinfo = decoded_value[:at_index]
        authority_suffix = decoded_value[at_index + 1 :]
        if ":" in userinfo and "/" in authority_suffix:
            return "userinfo"

    # Keep the established API semantics for URL-like values: raw query and
    # fragment components are rejected.  Also reject encoded delimiters in
    # the path after bounded multi-layer decoding, without rewriting safe
    # encoded paths in the accepted value.
    if not parsed.scheme and not parsed.netloc:
        return None
    if parsed.query or parsed.fragment or "?" in decoded_netloc or "#" in decoded_netloc:
        return "query"
    decoded_path = _fully_percent_decode(parsed.path)
    if decoded_path is None:
        return "encoded"
    if "?" in decoded_path or "#" in decoded_path:
        return "query"
    return None


def _object_values(
    value: Any,
    *,
    names: frozenset[str] = _SOURCE_FIELD_NAMES,
) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        # Read only known non-secret keys.  In particular, do not iterate a
        # Pydantic/dataclass dump: doing so would access api_key/token fields
        # even when they are later discarded.
        return {name: value[name] for name in names if name in value}
    values: dict[str, Any] = {}
    for name in names:
        try:
            item = getattr(value, name)
        except AttributeError:
            continue
        except Exception:
            continue
        if callable(item):
            continue
        values[name] = item
    return values


def _get_first(values: Mapping[str, Any], aliases: Sequence[str], default: Any = _OMITTED) -> Any:
    for alias in aliases:
        if alias in values:
            return values[alias]
    return default


def _assert_safe_name(name: str) -> None:
    if _SECRET_NAME.search(name):
        raise RunConfigurationError(
            "Secret-bearing configuration fields are not accepted",
            code="secret_field",
        )


def _json_safe(value: Any, *, name: str) -> Any:
    _assert_safe_name(name)
    if isinstance(value, SecretStr):
        raise RunConfigurationError(
            "Secret-bearing configuration is not recorded", code="secret_value"
        )
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, name=name) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise RunConfigurationError(
                "Requested configuration contains a non-JSON value",
                code="non_json_value",
            ) from error
        return value
    raise RunConfigurationError(
        "Requested configuration contains an unsupported value",
        code="non_json_value",
    )


def _validate_parameter_type(name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, SecretStr):
        raise RunConfigurationError(
            "Secret-bearing configuration is not recorded", code="secret_value"
        )
    string_fields = {"reasoning_effort", "service_tier"}
    integer_fields = {
        "seed",
        "max_output_tokens",
        "output_tokens",
        "max_evidence_chars",
        "max_evidence_lines",
        "max_lines_per_evidence",
        "evidence_chars",
        "evidence_lines",
    }
    numeric_fields = {"temperature", "timeout_seconds"}
    boolean_fields = {"response_format_json"}
    if name == "retry_policy" and value is not None:
        _validate_retry_policy(value)
        return
    valid = (
        (name in string_fields and isinstance(value, str))
        or (name in integer_fields and isinstance(value, int) and not isinstance(value, bool))
        or (
            name in numeric_fields
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        or (name in boolean_fields and isinstance(value, bool))
    )
    if not valid:
        raise RunConfigurationError(
            "Requested parameter has an invalid type", code="invalid_parameter"
        )


def normalize_endpoint(endpoint: str | None) -> str | None:
    """Normalize an API endpoint while rejecting userinfo/query/fragment."""

    if endpoint is None:
        return None
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise RunConfigurationError("Endpoint must be a non-empty URL", code="invalid_endpoint")
    value = endpoint.strip()
    violation = _url_security_violation(value)
    if violation == "userinfo":
        raise RunConfigurationError("Endpoint userinfo is not permitted", code="endpoint_userinfo")
    if violation == "invalid":
        raise RunConfigurationError("Endpoint must be a valid URL", code="invalid_endpoint")
    if violation in {"encoded", "query"}:
        raise RunConfigurationError(
            "Endpoint query/fragment is not permitted", code="endpoint_query"
        )
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise RunConfigurationError(
            "Endpoint must be a valid URL", code="invalid_endpoint"
        ) from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RunConfigurationError("Endpoint must use an HTTP(S) URL", code="invalid_endpoint")
    try:
        port = parsed.port
    except ValueError as error:
        raise RunConfigurationError("Endpoint port is invalid", code="invalid_endpoint") from error
    host = parsed.hostname.lower().rstrip(".")
    if not host:
        raise RunConfigurationError("Endpoint host is missing", code="invalid_endpoint")
    host_part = host
    if port is not None and port not in {80, 443}:
        host_part = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{host_part}{path}"


def _module_from_engine(engine: Any) -> tuple[ModuleType | None, str | None, str | None]:
    explicit_package: str | None = None
    explicit_module: str | None = None
    if isinstance(engine, Mapping):
        package = engine.get("package_name") or engine.get("package")
        module_name = engine.get("module") or engine.get("module_name")
        explicit_package = str(package) if package else None
        explicit_module = str(module_name) if module_name else None
        engine = explicit_module or explicit_package
    if isinstance(engine, ModuleType):
        return engine, explicit_package, explicit_module
    if isinstance(engine, str):
        if "/" not in engine and "\\" not in engine:
            try:
                module = importlib.import_module(engine)
                return module, explicit_package, explicit_module
            except (ImportError, ModuleNotFoundError):
                return None, engine, explicit_module
        return None, explicit_package, explicit_module
    if engine is not None:
        module = inspect.getmodule(engine)
        if module is None:
            module = inspect.getmodule(type(engine))
        return module, explicit_package, explicit_module
    try:
        module = importlib.import_module("repo_issue_intelligence")
    except ImportError:
        module = None
    return module, explicit_package, explicit_module


def _distribution_for_module(module: ModuleType | None, explicit_package: str | None) -> str | None:
    if explicit_package:
        return explicit_package
    if module is None:
        return None
    module_name = module.__name__
    top_level = module_name.split(".", 1)[0]
    try:
        distributions = importlib.metadata.packages_distributions().get(top_level, [])
    except Exception:
        distributions = []
    if "repo-issue-intelligence" in distributions:
        return "repo-issue-intelligence"
    return distributions[0] if distributions else top_level


def _source_git_provenance(source: Path | None) -> tuple[Path | None, str | None, bool | None, str]:
    if source is None:
        return None, None, None, "unknown"
    start = source if source.is_dir() else source.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except OSError:
        return None, None, None, "installed"
    if result.returncode != 0 or not result.stdout.strip():
        return None, None, None, "installed"
    try:
        source_root = Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None, None, None, "unknown"
    try:
        revision = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD^{commit}"],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except OSError:
        return source_root, None, None, "unknown"
    if revision.returncode != 0 or not revision.stdout.strip():
        return source_root, None, None, "git"
    try:
        relative = source.relative_to(source_root)
    except ValueError:
        relative = None
    status_args = ["git", "-C", str(source_root), "status", "--porcelain=v1", "-z"]
    if relative is not None:
        # For a package ``__init__`` file the imported engine consists of the
        # whole package directory.  Looking only at the module file would
        # incorrectly call an editable checkout clean when a sibling module
        # has changed or is newly added.
        scope = relative.parent if source.name == "__init__.py" else relative
        status_args.extend(["--", scope.as_posix()])
    try:
        status = subprocess.run(
            status_args,
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except OSError:
        return source_root, revision.stdout.strip(), None, "unknown"
    if status.returncode != 0:
        return source_root, revision.stdout.strip(), None, "unknown"
    dirty = bool(status.stdout)
    return source_root, revision.stdout.strip(), dirty, "git"


def capture_engine_runtime(
    engine: Any = None,
    *,
    package_name: str | None = None,
) -> EngineRuntime:
    """Record metadata and Git identity from the imported engine's source path."""

    module, discovered_package, explicit_module = _module_from_engine(engine)
    distribution = package_name or _distribution_for_module(module, discovered_package)
    imported_source: Path | None = None
    imported_module = explicit_module or (module.__name__ if module is not None else None)
    if module is not None:
        module_file = getattr(module, "__file__", None)
        if module_file:
            try:
                imported_source = Path(module_file).resolve(strict=True)
            except OSError:
                imported_source = Path(module_file).expanduser().resolve()
    package_version: str | None = None
    if distribution:
        try:
            package_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            package_version = None
        except Exception:
            package_version = None
    source_root, source_revision, source_dirty, provenance = _source_git_provenance(imported_source)
    return EngineRuntime(
        package_name=distribution,
        package_version=package_version,
        imported_module=imported_module,
        imported_source=imported_source,
        source_root=source_root,
        source_revision=source_revision,
        source_dirty=source_dirty,
        source_provenance=provenance,
        python_version=sys.version,
        python_implementation=platform.python_implementation(),
        runtime=platform.platform(),
    )


def _client_model(values: Mapping[str, Any]) -> str | None:
    value = _get_first(values, _MODEL_ALIASES)
    if value is _OMITTED or value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RunConfigurationError(
            "requested_model must be a non-empty string", code="invalid_model"
        )
    return value.strip()


def _safe_cli_flags(flags: Sequence[str] | None) -> tuple[str, ...]:
    if flags is None:
        return ()
    if isinstance(flags, (str, bytes)):
        raise RunConfigurationError("CLI flags must be a sequence", code="invalid_cli_flags")
    safe: list[str] = []
    for flag in flags:
        if not isinstance(flag, str) or not flag.strip():
            raise RunConfigurationError(
                "CLI flags must be non-empty strings", code="invalid_cli_flags"
            )
        if _SECRET_NAME.search(flag) or "?" in flag or "#" in flag:
            raise RunConfigurationError(
                "Secret-bearing CLI flags are not accepted", code="secret_cli_flag"
            )
        if _url_security_violation(flag) is not None:
            raise RunConfigurationError(
                "Secret-bearing CLI flags are not accepted", code="secret_cli_flag"
            )
        if "=" in flag:
            candidate = flag.split("=", 1)[1].strip()
            if _url_security_violation(candidate) is not None:
                raise RunConfigurationError(
                    "Secret-bearing CLI flags are not accepted", code="secret_cli_flag"
                )
        safe.append(flag)
    return tuple(safe)


def _validate_retry_policy(value: Any) -> dict[str, Any]:
    """Validate the closed retry-policy value contract.

    Retry policy is recorded as behavior, not as an arbitrary JSON metadata
    bag.  Keeping the accepted keys and value shapes closed also prevents a
    caller from smuggling a URL or credential-bearing object through the
    otherwise generic JSON serializer.
    """

    if not isinstance(value, Mapping):
        raise RunConfigurationError(
            "retry_policy must be a mapping", code="invalid_retry_policy"
        )
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _RETRY_POLICY_FIELDS:
            raise RunConfigurationError(
                "retry_policy contains an unsupported field",
                code="invalid_retry_policy",
            )
        if key == "max_attempts":
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                raise RunConfigurationError(
                    "retry_policy max_attempts must be a positive integer",
                    code="invalid_retry_policy",
                )
            normalized[key] = item
            continue
        if not isinstance(item, (list, tuple)):
            raise RunConfigurationError(
                "retry_policy sequence field has an invalid type",
                code="invalid_retry_policy",
            )
        if key == "backoff":
            if any(
                isinstance(entry, bool)
                or not isinstance(entry, (int, float))
                or not math.isfinite(entry)
                or entry < 0
                for entry in item
            ):
                raise RunConfigurationError(
                    "retry_policy backoff must contain finite non-negative numbers",
                    code="invalid_retry_policy",
                )
            normalized[key] = list(item)
            continue
        if any(
            not isinstance(entry, str) or entry not in _RETRY_POLICY_CATEGORIES
            for entry in item
        ):
            raise RunConfigurationError(
                "retry_policy categories contains an unsupported value",
                code="invalid_retry_policy",
            )
        normalized[key] = list(item)
    return normalized


def _as_budget(values: Mapping[str, Any]) -> BudgetConfiguration:
    source = _object_values(values)
    def value(*names: str) -> Any:
        found = _get_first(source, names)
        return None if found is _OMITTED else found
    retry = value(*_BUDGET_ALIASES["retry_policy"])
    if retry is None:
        retry = {}
    retry = _validate_retry_policy(retry)
    return BudgetConfiguration(
        output_tokens=value(*_BUDGET_ALIASES["output_tokens"]),
        evidence_chars=value(*_BUDGET_ALIASES["evidence_chars"]),
        evidence_lines=value(*_BUDGET_ALIASES["evidence_lines"]),
        timeout_seconds=value(*_BUDGET_ALIASES["timeout_seconds"]),
        retry_policy=freeze_mapping(_json_safe(retry, name="retry_policy")),
    )


def _validated_budget_model(value: BudgetConfiguration) -> BudgetConfiguration:
    """Revalidate direct budget models before capture.

    Normal Pydantic construction already validates scalar budget fields, but a
    caller can provide a model created with ``model_construct``.  Rebuilding
    through the public model constructor ensures the nested retry policy is
    frozen and validated without trusting the model as a serialization
    boundary.  Restore ``model_fields_set`` so omitted versus explicit null
    provenance remains intact.
    """

    retry = _validate_retry_policy(value.retry_policy)
    try:
        validated = BudgetConfiguration(
            output_tokens=value.output_tokens,
            evidence_chars=value.evidence_chars,
            evidence_lines=value.evidence_lines,
            timeout_seconds=value.timeout_seconds,
            retry_policy=freeze_mapping(_json_safe(retry, name="retry_policy")),
        )
    except ValueError as error:
        raise RunConfigurationError(
            "Budget configuration has an invalid value", code="invalid_budget"
        ) from error
    object.__setattr__(validated, "__pydantic_fields_set__", set(value.model_fields_set))
    return validated


def _validate_budget_keys(values: Mapping[str, Any]) -> None:
    allowed = {alias for aliases in _BUDGET_ALIASES.values() for alias in aliases}
    for key in values:
        name = str(key)
        if name not in allowed:
            if _SECRET_NAME.search(name):
                raise RunConfigurationError(
                    "Secret-bearing configuration fields are not accepted",
                    code="secret_field",
                )
            raise RunConfigurationError("Unsupported budget field", code="invalid_budget")


def _canonicalize_budget_layer(values: Mapping[str, Any]) -> dict[str, Any]:
    source = _object_values(values)
    canonical: dict[str, Any] = {}
    for field, aliases in _BUDGET_ALIASES.items():
        present = [(alias, source[alias]) for alias in aliases if alias in source]
        if not present:
            continue
        first_value = present[0][1]
        if any(value != first_value for _, value in present[1:]):
            raise RunConfigurationError(
                "Conflicting aliases in one budget source", code="invalid_budget"
            )
        canonical[field] = first_value
    return canonical


def _record_budget_origins(
    origins: dict[str, str],
    *,
    budget: BudgetConfiguration,
    budgets: Mapping[str, Any] | BudgetConfiguration | None,
    budget_source: Mapping[str, Any],
    explicit_parameters: Mapping[str, Any],
    defaults: Mapping[str, Any],
    client_values: Mapping[str, Any],
) -> None:
    for field, aliases in _BUDGET_ALIASES.items():
        if isinstance(budgets, BudgetConfiguration):
            if field in budgets.model_fields_set:
                origins[field] = ParameterOrigin.USER_CONFIG.value
            continue
        source_key: str | None = None
        source_origin: str | None = None
        if budgets is not None:
            source_values = _object_values(budget_source)
            for alias in aliases:
                if alias in source_values:
                    source_key = alias
                    source_origin = ParameterOrigin.USER_CONFIG.value
                    break
        else:
            for alias in aliases:
                if alias in explicit_parameters:
                    source_key = alias
                    source_origin = ParameterOrigin.USER_CONFIG.value
                    break
            if source_key is None:
                for alias in aliases:
                    if alias in defaults or alias in client_values:
                        source_key = alias
                        source_origin = ParameterOrigin.CLIENT_DEFAULT.value
                        break
        if source_key is None or source_origin is None:
            continue
        origins[field] = source_origin
        if source_key in _SAFE_PARAMETER_NAMES:
            origins[source_key] = source_origin


def _as_protocol(values: Mapping[str, Any] | None) -> ProtocolConfiguration:
    source = _object_values(values)
    aliases = {
        "provider_schema_version": ("provider_schema_version", "provider_schema"),
        "prompt_version": ("prompt_version",),
        "analysis_protocol_version": ("analysis_protocol_version", "analysis_version"),
        "retrieval_protocol_version": ("retrieval_protocol_version", "retrieval_version"),
        "index_version": ("index_version",),
        "top_k": ("top_k",),
        "selection_protocol": ("selection_protocol",),
    }
    kwargs: dict[str, Any] = {}
    for field, names in aliases.items():
        found = _get_first(source, names)
        if found is not _OMITTED:
            kwargs[field] = found
    return ProtocolConfiguration(**kwargs)


def capture_requested_run_configuration(
    client: Any = None,
    engine: Any = None,
    budgets: Mapping[str, Any] | BudgetConfiguration | None = None,
    *,
    requested_model: str | None | _Omitted = _OMITTED,
    request_parameters: Mapping[str, Any] | None = None,
    client_defaults: Mapping[str, Any] | None = None,
    parameter_origins: Mapping[str, str | ParameterOrigin] | None = None,
    protocol: Mapping[str, Any] | ProtocolConfiguration | None = None,
    safe_cli_flags: Sequence[str] | None = None,
    captured_at: datetime | None = None,
    package_name: str | None = None,
) -> RunConfiguration:
    """Capture requested settings with explicit ``user/default/omitted`` origins.

    ``client`` is treated as a source of safe client defaults.  Callers express
    user intent through the keyword arguments or ``request_parameters``.  An
    explicit ``None`` in either keyword is retained as a requested null; an
    omitted keyword is represented only by ``parameter_origins`` and is not
    silently converted into a provider/effective value.
    """

    client_values = _object_values(client)
    defaults = _object_values(client_defaults)
    embedded_requested = client_values.get("requested")
    if isinstance(embedded_requested, Mapping):
        client_values = {**client_values, **embedded_requested}
    embedded_parameters = client_values.get("request_parameters")
    if embedded_parameters is not None and not isinstance(embedded_parameters, Mapping):
        raise RunConfigurationError(
            "request_parameters must be a mapping", code="invalid_parameters"
        )
    explicit_parameters: dict[str, Any] = {}
    if embedded_parameters:
        explicit_parameters.update(embedded_parameters)
    if request_parameters is not None:
        explicit_parameters.update(request_parameters)
    for name in explicit_parameters:
        if name not in _SAFE_PARAMETER_NAMES:
            _assert_safe_name(name)
            raise RunConfigurationError(
                f"Unsupported requested parameter: {name}",
                code="unsupported_parameter",
            )
    safe_parameters: dict[str, Any] = {}
    origins: dict[str, str] = {}
    for name in sorted(_SAFE_PARAMETER_NAMES):
        if name in explicit_parameters:
            parameter_value = explicit_parameters[name]
            _validate_parameter_type(name, parameter_value)
            if name == "retry_policy" and parameter_value is not None:
                parameter_value = _validate_retry_policy(parameter_value)
            safe_parameters[name] = _json_safe(parameter_value, name=name)
            origins[name] = ParameterOrigin.USER_CONFIG.value
            continue
        aliases = _PARAMETER_ALIASES.get(name, (name,))
        default_value = _get_first(defaults, aliases)
        client_value = _get_first(client_values, aliases)
        if default_value is not _OMITTED:
            _validate_parameter_type(name, default_value)
            parameter_value = default_value
            if name == "retry_policy" and parameter_value is not None:
                parameter_value = _validate_retry_policy(parameter_value)
            safe_parameters[name] = _json_safe(parameter_value, name=name)
            origins[name] = ParameterOrigin.CLIENT_DEFAULT.value
        elif client_value is not _OMITTED:
            _validate_parameter_type(name, client_value)
            parameter_value = client_value
            if name == "retry_policy" and parameter_value is not None:
                parameter_value = _validate_retry_policy(parameter_value)
            safe_parameters[name] = _json_safe(parameter_value, name=name)
            origins[name] = ParameterOrigin.CLIENT_DEFAULT.value
        else:
            origins[name] = ParameterOrigin.OMITTED.value
    if requested_model is not _OMITTED:
        model = requested_model
        model_origin = ParameterOrigin.USER_CONFIG.value
        model_captured = True
    else:
        default_model = _get_first(defaults, _MODEL_ALIASES)
        client_model = _get_first(client_values, _MODEL_ALIASES)
        if default_model is not _OMITTED:
            model = default_model
            model_origin = ParameterOrigin.CLIENT_DEFAULT.value
            model_captured = True
        elif client_model is not _OMITTED:
            model = client_model
            model_origin = ParameterOrigin.CLIENT_DEFAULT.value
            model_captured = True
        else:
            model = None
            model_origin = ParameterOrigin.OMITTED.value
            model_captured = False
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise RunConfigurationError(
            "requested_model must be a non-empty string", code="invalid_model"
        )
    origins["requested_model"] = model_origin

    if parameter_origins:
        allowed_origin_names = _SAFE_PARAMETER_NAMES | {"requested_model"}
        for name, origin in parameter_origins.items():
            if str(name) not in allowed_origin_names:
                raise RunConfigurationError(
                    "Unknown parameter origin field", code="invalid_origin"
                )
            try:
                normalized_origin = ParameterOrigin(origin).value
            except ValueError as error:
                raise RunConfigurationError(
                    "Unknown parameter origin", code="invalid_origin"
                ) from error
            field_name = str(name)
            has_value = field_name in safe_parameters or (
                field_name == "requested_model" and model_captured
            )
            if normalized_origin == ParameterOrigin.OMITTED.value and has_value:
                raise RunConfigurationError(
                    "Omitted parameters cannot have a captured value",
                    code="invalid_origin",
                )
            if normalized_origin != ParameterOrigin.OMITTED.value and not has_value:
                raise RunConfigurationError(
                    "Non-omitted parameters must have a captured value",
                    code="invalid_origin",
                )
            origins[field_name] = normalized_origin

    backend_value = _get_first(client_values, _BACKEND_ALIASES)
    if backend_value is _OMITTED:
        backend_value = _get_first(defaults, _BACKEND_ALIASES, default="api")
    provider_value = _get_first(client_values, _PROVIDER_ALIASES)
    if provider_value is _OMITTED:
        provider_value = _get_first(defaults, _PROVIDER_ALIASES)
    endpoint_value = _get_first(client_values, _ENDPOINT_ALIASES)
    if endpoint_value is _OMITTED:
        endpoint_value = _get_first(defaults, _ENDPOINT_ALIASES)
    backend = str(backend_value) if backend_value is not _OMITTED else "api"
    provider = (
        None
        if provider_value is _OMITTED or provider_value is None
        else str(provider_value)
    )
    endpoint = (
        None
        if endpoint_value is _OMITTED or endpoint_value is None
        else normalize_endpoint(str(endpoint_value))
    )
    cli_executable_value = _get_first(
        client_values, ("cli_executable", "codex_cli_executable")
    )
    if cli_executable_value is _OMITTED:
        cli_executable_value = _get_first(
            defaults, ("cli_executable", "codex_cli_executable")
        )
    cli_version_value = _get_first(client_values, ("cli_version", "codex_cli_version"))
    if cli_version_value is _OMITTED:
        cli_version_value = _get_first(defaults, ("cli_version", "codex_cli_version"))
    response_format = _get_first(
        client_values, ("response_format_json", "llm_response_format_json")
    )
    if response_format is _OMITTED:
        response_format = _get_first(
            defaults, ("response_format_json", "llm_response_format_json")
        )
    client_config = ClientConfiguration(
        backend=backend,
        provider=provider,
        endpoint=endpoint,
        cli_executable=(None if cli_executable_value is _OMITTED else str(cli_executable_value)),
        cli_version=(None if cli_version_value is _OMITTED else str(cli_version_value)),
        transport="cli" if backend == "codex-cli" else "api",
        response_format_json=(None if response_format is _OMITTED else bool(response_format)),
    )
    if budgets is None:
        budget_source = {
            **_canonicalize_budget_layer(defaults),
            **_canonicalize_budget_layer(client_values),
            **_canonicalize_budget_layer(explicit_parameters),
        }
        budget_values = budget_source
    elif isinstance(budgets, BudgetConfiguration):
        budget = _validated_budget_model(budgets)
        budget_source = {}
        budget_values = {}
    else:
        if not isinstance(budgets, Mapping):
            raise RunConfigurationError("budgets must be a mapping", code="invalid_budget")
        _validate_budget_keys(budgets)
        budget_source = budgets
        budget_values = _canonicalize_budget_layer(budgets)
    if not isinstance(budgets, BudgetConfiguration):
        budget = _as_budget(budget_values)
    _record_budget_origins(
        origins,
        budget=budget,
        budgets=budgets,
        budget_source=budget_source,
        explicit_parameters=explicit_parameters,
        defaults=defaults,
        client_values=client_values,
    )
    safe_flags_source = safe_cli_flags
    if safe_flags_source is None:
        embedded_flags = _get_first(client_values, ("safe_cli_flags", "cli_flags"))
        safe_flags_source = None if embedded_flags is _OMITTED else embedded_flags
    flags = _safe_cli_flags(safe_flags_source)
    protocol_config = (
        protocol
        if isinstance(protocol, ProtocolConfiguration)
        else _as_protocol(protocol)
    )
    timestamp = captured_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise RunConfigurationError("captured_at must be timezone-aware", code="invalid_timestamp")
    runtime = capture_engine_runtime(engine, package_name=package_name)
    return RunConfiguration(
        client=client_config,
        requested_model=(None if model is None else str(model).strip()),
        request_parameters=freeze_mapping(safe_parameters),
        parameter_origins=freeze_mapping(origins),
        budgets=budget,
        protocol=protocol_config,
        engine=runtime,
        safe_cli_flags=flags,
        captured_at=timestamp.astimezone(UTC),
    )


__all__ = [
    "ConfigurationValueSource",
    "RunConfigurationError",
    "capture_engine_runtime",
    "capture_requested_run_configuration",
    "normalize_endpoint",
]
