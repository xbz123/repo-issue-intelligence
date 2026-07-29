from __future__ import annotations

import ast
import json
import os
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .models import FileRecord, RepositoryMap, SymbolRecord

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
}
LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
}
RUNTIME_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "go.mod",
    "Cargo.toml",
}
ENTRYPOINT_NAMES = {"main.py", "app.py", "server.py", "manage.py", "cli.py", "index.ts", "index.js"}
FRAMEWORK_IMPORTS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "typer": "Typer",
    "celery": "Celery",
    "langgraph": "LangGraph",
}


def _python_metadata(path: Path) -> tuple[list[SymbolRecord], list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return [], []
    symbols: list[SymbolRecord] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind="function",
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", None),
                    docstring=ast.get_docstring(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind="class",
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", None),
                    docstring=ast.get_docstring(node),
                )
            )
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return sorted(symbols, key=lambda item: item.line), sorted(set(imports))


def _repository_files(
    root: Path,
    included_files: Iterable[str] | None,
) -> Iterable[tuple[Path, Path]]:
    if included_files is not None:
        for value in sorted(set(included_files)):
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Repository file must be relative to the root: {value}")
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file():
                yield path, relative
        return

    for current_root, dirs, filenames in os.walk(root):
        dirs[:] = [directory for directory in dirs if directory not in SKIP_DIRS]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            yield path, path.relative_to(root)


def build_repository_map(
    root: Path,
    included_files: Iterable[str] | None = None,
) -> RepositoryMap:
    root = root.resolve()
    files: list[FileRecord] = []
    languages: Counter[str] = Counter()
    frameworks: set[str] = set()
    entrypoints: list[str] = []
    runtime_files: list[str] = []
    test_directories: set[str] = set()
    for path, relative in _repository_files(root, included_files):
        filename = path.name
        for index, part in enumerate(relative.parts[:-1]):
            if part in {"test", "tests"}:
                test_directories.add(str(Path(*relative.parts[: index + 1])))
        if filename in RUNTIME_FILES:
            runtime_files.append(str(relative))
        if filename in ENTRYPOINT_NAMES:
            entrypoints.append(str(relative))
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        if not language:
            continue
        languages[language] += 1
        symbols, imports = ([], [])
        if language == "Python":
            symbols, imports = _python_metadata(path)
            for imported in imports:
                framework = FRAMEWORK_IMPORTS.get(imported.split(".", maxsplit=1)[0])
                if framework:
                    frameworks.add(framework)
        files.append(
            FileRecord(
                path=str(relative),
                language=language,
                symbols=symbols,
                imports=imports,
                test_file="test" in filename.lower() or "tests" in relative.parts,
            )
        )
    return RepositoryMap(
        root=str(root),
        languages=dict(languages.most_common()),
        frameworks=sorted(frameworks),
        entrypoints=sorted(entrypoints),
        test_directories=sorted(test_directories),
        runtime_files=sorted(runtime_files),
        files=sorted(files, key=lambda file: file.path),
    )


def save_repository_map(repository_map: RepositoryMap, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(repository_map.model_dump(), indent=2), encoding="utf-8")
