"""Private JSON publication for V2 CLI and evaluation summaries."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path


def validate_private_export_output(output: Path, databases: Sequence[Path] = ()) -> None:
    suffixes = ("-wal", "-shm", "-journal", ".writer.lock", ".legacy.json")
    try:
        resolved = output.resolve()
        for database in databases:
            protected = [database, *(database.with_name(database.name + s) for s in suffixes)]
            for path in protected:
                if resolved == path.resolve() or (
                    output.exists() and path.exists() and output.samefile(path)
                ):
                    raise ValueError("V2 output must not replace the database or its sidecars")
        if output.exists():
            if not output.is_file():
                raise ValueError("V2 output must be a regular file")
            with output.open("rb") as existing:
                if existing.read(16) == b"SQLite format 3\x00":
                    raise ValueError("V2 output must not replace a SQLite database")
    except (OSError, RuntimeError):
        raise ValueError("V2 output cannot be safely resolved") from None


def write_private_json(output: Path, payload: str, databases: Sequence[Path] = ()) -> None:
    validate_private_export_output(output, databases)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        validate_private_export_output(output, databases)
        # Replace the directory entry, never truncate through an output symlink/hardlink.
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
