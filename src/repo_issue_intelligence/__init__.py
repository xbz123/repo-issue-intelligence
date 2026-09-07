"""Repository-aware issue intelligence.

The Protocol v2 helpers are exported as explicit capture utilities.  Merely
importing the package keeps the established V1 AgentStore/workflow path
unchanged; no repository or configuration capture happens implicitly.
"""

from importlib import metadata

try:
    __version__ = metadata.version("repo-issue-intelligence")
except metadata.PackageNotFoundError:
    # Source checkouts without an installed distribution still have a stable
    # project version, while runtime provenance records the metadata result as
    # unknown rather than inventing a provider/effective version.
    __version__ = "0.5.0"

from .protocol_v2_models import (  # noqa: E402  (exports after version lookup)
    RepositoryCaptureMode,
    RepositorySnapshot,
    RunConfiguration,
    RunInputs,
)
from .repository_context import capture_repository_context  # noqa: E402
from .repository_view import (  # noqa: E402
    DeterministicResumeError,
    RepositoryView,
    RepositoryViewError,
    prepare_repository_view,
)
from .run_configuration import (  # noqa: E402
    capture_engine_runtime,
    capture_requested_run_configuration,
)

__all__ = [
    "RepositoryCaptureMode",
    "RepositorySnapshot",
    "RunConfiguration",
    "RunInputs",
    "__version__",
    "capture_engine_runtime",
    "capture_repository_context",
    "capture_requested_run_configuration",
    "DeterministicResumeError",
    "RepositoryView",
    "RepositoryViewError",
    "prepare_repository_view",
]
