"""Trusted-local HTTP boundary; application recovery services do not import it."""

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from secrets import compare_digest
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse

from .agent_database import _private_destination
from .agent_store import AgentStore
from .config import Settings
from .repository_index import SKIP_DIRS
from .run_configuration import normalize_endpoint

MAX_REQUEST_BYTES = 1_048_576
MAX_ISSUES = 100
MAX_TEXT_CHARS = 100_000
MAX_TOTAL_TEXT_CHARS = 250_000
MAX_PAGE_SIZE = 100
MAX_SOURCE_FILE_BYTES = 2_000_000
MAX_SOURCE_BYTES = 32_000_000
MAX_SOURCE_ENTRIES = 20_000


def _check_payload(value):
    if isinstance(value, dict) and isinstance(value.get("issues"), list):
        if len(value["issues"]) > MAX_ISSUES:
            raise HTTPException(413, "Too many Issues")
    pending = [(value, 0)]
    text_size = 0
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 20_000:
            raise HTTPException(413, "Request structure exceeds limits")
        if isinstance(item, str):
            text_size += len(item)
            if len(item) > MAX_TEXT_CHARS or text_size > MAX_TOTAL_TEXT_CHARS:
                raise HTTPException(413, "Request text exceeds limits")
        elif isinstance(item, dict):
            pending.extend((child, depth + 1) for child in (*item.keys(), *item.values()))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


@dataclass(frozen=True)
class LocalPrincipal:
    name: str


def _configured_token(settings: Settings) -> str:
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    return (
        token
        if len(token) >= 32 and token.isascii() and not any(c.isspace() for c in token)
        else ""
    )


def private_legacy_store() -> AgentStore:
    """Protect the HTTP legacy ledger without migrating it or changing CLI defaults."""
    try:
        path = Settings().agent_db_path.absolute()
        if os.name != "posix" or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError
        if not path.exists():
            path = _private_destination(path)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        for target in (
            path.parent,
            path,
            *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
        ):
            if not os.path.lexists(target):
                continue
            info = target.lstat()
            if (
                info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or (
                    not stat.S_ISDIR(info.st_mode)
                    if target == path.parent
                    else not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                )
            ):
                raise ValueError
        return AgentStore(path)
    except (OSError, ValueError):
        raise HTTPException(503, "Private local database unavailable") from None


def authorize_repository_operation(
    principal: LocalPrincipal,
    scope: Path | str,
    operation: str,
    *,
    provider: str | None = None,
) -> Path:
    """Recheck current HTTP scope policy; callers supply the authenticated principal."""
    try:
        settings = Settings()
        root = Path(scope)
        if (
            not _configured_token(settings)
            or principal.name != settings.api_principal
            or operation
            not in {"index", "run", "read", "review", "evidence", "retry", "recover-unknown"}
            or operation not in settings.api_operations
            or not root.is_absolute()
            or ".." in root.parts
        ):
            raise HTTPException(403, "Repository operation not permitted")
        allowed = [
            candidate
            for candidate in settings.api_analysis_roots
            if candidate.is_absolute()
            and ".." not in candidate.parts
            and root.is_relative_to(candidate)
        ]
        if not allowed or any(path.is_symlink() for path in (root, *root.parents)):
            raise HTTPException(403, "Repository scope not permitted")
        if operation in {"index", "run"} and not root.is_dir():
            raise HTTPException(404, "Repository scope unavailable")
        if operation in {"retry", "recover-unknown"} or provider is not None:
            current_provider = (
                settings.llm_api_provider if settings.llm_backend == "api" else "codex-cli"
            )
            if (
                provider is None
                or provider != current_provider
                or not any(
                    grant.principal == principal.name
                    and grant.operation == operation
                    and grant.provider == provider
                    and grant.analysis_root.is_absolute()
                    and ".." not in grant.analysis_root.parts
                    and root.is_relative_to(grant.analysis_root)
                    for grant in settings.api_external_grants
                )
            ):
                raise HTTPException(403, "External transfer not permitted")
            if settings.llm_backend == "api":
                normalize_endpoint(settings.llm_api_base_url)
        # Git metadata never broadens the authorized source subtree.
        if operation in {"index", "run"}:

            def unreadable(error):
                raise error

            size = entries = 0
            for current, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
                directories[:] = [name for name in directories if name not in SKIP_DIRS]
                for name in (*directories, *files):
                    info = (Path(current) / name).lstat()
                    entries += 1
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise HTTPException(403, "Repository scope refuses links or special files")
                    if stat.S_ISREG(info.st_mode):
                        size += info.st_size
                        if info.st_size > MAX_SOURCE_FILE_BYTES or size > MAX_SOURCE_BYTES:
                            raise HTTPException(413, "Repository source exceeds limits")
                    if entries > MAX_SOURCE_ENTRIES:
                        raise HTTPException(413, "Repository entry count exceeds limits")
        return root
    except (OSError, ValueError):
        raise HTTPException(403, "Repository scope unavailable or unsafe") from None


def _local_origin(origin: str) -> bool:
    try:
        value = urlsplit(origin)
        host = value.hostname
        return (
            value.scheme in {"http", "https"}
            and value.username is None
            and value.password is None
            and not value.path
            and not value.query
            and not value.fragment
            and value.port != 0
            and "%" not in origin
            and (host == "localhost" or (host is not None and ip_address(host).is_loopback))
        )
    except ValueError:
        return False


def require_principal(request: Request) -> LocalPrincipal | None:
    if request.url.path == "/health":
        return None
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, LocalPrincipal):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return principal


class APIBoundary:
    def __init__(self, app):
        self.app = app
        # ponytail: one state-building request per process; multi-worker serving is unsupported.
        self.work_slot = BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            settings = Settings()
        except ValueError:
            await JSONResponse({"detail": "Server configuration unavailable"}, 503)(
                scope, receive, send
            )
            return
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        origins = headers.getlist("origin")
        own_origin = f"{scope['scheme']}://{hosts[0]}" if len(hosts) == 1 else ""
        if (
            not _local_origin(own_origin)
            or any(name == "forwarded" or name.startswith("x-forwarded-") for name in headers)
            or (
                origins
                and (
                    len(origins) != 1
                    or not _local_origin(origins[0])
                    or origins[0] not in (own_origin, *settings.api_allowed_origins)
                )
            )
        ):
            await JSONResponse({"detail": "Request origin or proxy not permitted"}, 403)(
                scope, receive, send
            )
            return
        if scope["path"] != "/health":
            authorization = headers.getlist("authorization")
            token = _configured_token(settings)
            if (
                not token
                or len(authorization) != 1
                or not compare_digest(authorization[0].encode("utf-8"), f"Bearer {token}".encode())
            ):
                await JSONResponse(
                    {"detail": "Authentication required"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
            scope.setdefault("state", {})["principal"] = LocalPrincipal(settings.api_principal)
        try:
            query = QueryParams(scope.get("query_string", b""))
            for name in ("limit", "page_size"):
                for value in query.getlist(name):
                    if (
                        not value.isascii()
                        or not value.isdecimal()
                        or not 1 <= int(value) <= MAX_PAGE_SIZE
                    ):
                        raise HTTPException(413, "Pagination exceeds limits")
            lengths = headers.getlist("content-length")
            if len(lengths) > 1 or (
                lengths
                and (
                    not lengths[0].isascii()
                    or not lengths[0].isdecimal()
                    or int(lengths[0]) > MAX_REQUEST_BYTES
                )
            ):
                raise HTTPException(413, "Request body exceeds limits")
            body = bytearray()
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_REQUEST_BYTES:
                        raise HTTPException(413, "Request body exceeds limits")
                    if not message.get("more_body", False):
                        break
            if headers.get("content-encoding", "identity") != "identity":
                raise HTTPException(415, "Encoded request bodies are not supported")
            if body:
                _check_payload(json.loads(body))
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, error.status_code)(scope, receive, send)
            return
        except (ValueError, RecursionError):
            await JSONResponse({"detail": "Invalid or excessive JSON body"}, 413)(
                scope, receive, send
            )
            return
        except TimeoutError:
            await JSONResponse({"detail": "Request body timed out"}, 408)(scope, receive, send)
            return

        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        needs_slot = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if needs_slot and not self.work_slot.acquire(blocking=False):
            await JSONResponse(
                {"detail": "Local executor busy"}, 503, headers={"Retry-After": "1"}
            )(scope, receive, send)
            return
        try:
            await self.app(scope, bounded_receive, send)
        except Exception:
            # Raw filesystem/provider exception strings can contain credentials.
            await JSONResponse({"detail": "Local execution failed"}, 500)(scope, receive, send)
        finally:
            if needs_slot:
                self.work_slot.release()
