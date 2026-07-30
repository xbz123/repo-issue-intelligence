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


class _FunctionCallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return


def _qualified_symbol_name(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
) -> str:
    names = [node.name]
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(parent.name)
        parent = parents.get(parent)
    return ".".join(reversed(names))


def _python_metadata(
    path: Path,
) -> tuple[
    list[SymbolRecord],
    list[str],
    list[str],
    dict[str, list[str]],
    list[str],
]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return [], [], [], {}, []
    symbols: list[SymbolRecord] = []
    imports: list[str] = []
    calls: list[str] = []
    symbol_calls: dict[str, set[str]] = {}
    references: list[str] = []
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    qualified_name=_qualified_symbol_name(node, parents),
                    kind="function",
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", None),
                    docstring=ast.get_docstring(node),
                )
            )
            collector = _FunctionCallCollector()
            for statement in node.body:
                collector.visit(statement)
            symbol_calls.setdefault(node.name, set()).update(collector.calls)
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    qualified_name=_qualified_symbol_name(node, parents),
                    kind="class",
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", None),
                    docstring=ast.get_docstring(node),
                )
            )
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = f"{'.' * node.level}{node.module or ''}"
            if prefix:
                imports.append(prefix)
            for alias in node.names:
                candidate = (
                    f"{prefix}.{alias.name}"
                    if node.module
                    else f"{'.' * node.level}{alias.name}"
                )
                imports.append(candidate)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            references.append(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            references.append(node.attr)
    return (
        sorted(symbols, key=lambda item: item.line),
        sorted(set(imports)),
        sorted(set(calls)),
        {
            symbol: sorted(called)
            for symbol, called in sorted(symbol_calls.items())
            if called
        },
        sorted(set(references)),
    )


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


def _python_module(relative_path: str) -> tuple[str, str]:
    relative = Path(relative_path)
    parts = list(relative.with_suffix("").parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    if parts and parts[0] in {"src", "lib"}:
        parts.pop(0)
    module = ".".join(parts)
    package = module if is_package else module.rpartition(".")[0]
    return module, package


def _resolve_python_local_imports(files: list[FileRecord]) -> None:
    module_paths: dict[str, set[str]] = {}
    module_by_path: dict[str, tuple[str, str]] = {}
    for file in files:
        if file.language != "Python":
            continue
        module, package = _python_module(file.path)
        module_by_path[file.path] = (module, package)
        if module:
            module_paths.setdefault(module, set()).add(file.path)

    for file in files:
        module_info = module_by_path.get(file.path)
        if module_info is None:
            continue
        _, package = module_info
        resolved: set[str] = set()
        imported_symbols: dict[str, set[str]] = {}
        for imported in file.imports:
            level = len(imported) - len(imported.lstrip("."))
            suffix = imported[level:]
            if level:
                package_parts = package.split(".") if package else []
                parent_count = max(0, level - 1)
                if parent_count > len(package_parts):
                    continue
                base_parts = (
                    package_parts[: len(package_parts) - parent_count]
                    if parent_count
                    else package_parts
                )
                imported_parts = [*base_parts, *suffix.split(".")]
                candidate = ".".join(part for part in imported_parts if part)
            else:
                candidate = suffix

            parts = candidate.split(".")
            while parts:
                module_candidate = ".".join(parts)
                paths = module_paths.get(module_candidate)
                if paths:
                    resolved.update(paths)
                    symbol_suffix = candidate.removeprefix(module_candidate).lstrip(".")
                    if symbol_suffix:
                        symbol = symbol_suffix.split(".", maxsplit=1)[0]
                        for path in paths:
                            imported_symbols.setdefault(path, set()).add(symbol)
                    break
                parts.pop()
        file.local_imports = sorted(resolved - {file.path})
        file.local_import_symbols = {
            path: sorted(symbols)
            for path, symbols in sorted(imported_symbols.items())
            if path != file.path
        }


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
        symbols, imports, calls, symbol_calls, references = ([], [], [], {}, [])
        if language == "Python":
            symbols, imports, calls, symbol_calls, references = _python_metadata(path)
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
                calls=calls,
                symbol_calls=symbol_calls,
                references=references,
                test_file="test" in filename.lower() or "tests" in relative.parts,
            )
        )
    _resolve_python_local_imports(files)
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
