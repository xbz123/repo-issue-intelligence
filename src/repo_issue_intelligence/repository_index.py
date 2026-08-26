from __future__ import annotations

import ast
import json
import os
import re
import symtable
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    FileRecord,
    QualifiedExternalCall,
    RepositoryMap,
    ResolvedCall,
    SymbolRecord,
)

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
JSON_SCHEMA_SUFFIX = ".schema.json"
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

# Bump this whenever repository-map construction semantics change. Benchmark
# caches use the value as a fail-closed invalidation boundary.
REPOSITORY_MAP_INDEX_VERSION = 14

RUST_FUNCTION_DECLARATION = re.compile(
    r"^\s*(?:(?:pub(?:\s*\([^)]*\))?|async|const|unsafe|default)\s+)*"
    r'(?:extern(?:\s+(?:"[^"]+"|value))?\s+)?'
    r"fn\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
RUST_TYPE_DECLARATION = re.compile(
    r"^\s*(?:(?:pub(?:\s*\([^)]*\))?|unsafe|auto)\s+)*"
    r"(?P<kind>struct|enum|trait|type|union)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
RUST_CHAR_LITERAL = re.compile(
    r"'(?:\\(?:x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f_]+\}|.)|[^\\'\r\n])'"
)
RUST_MACRO_INVOCATION = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?:(?:(?:r#)?[A-Za-z_][A-Za-z0-9_]*)\s*::\s*)*"
    r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*\s*!(?!=)"
)
RUST_DELIMITER_PAIRS = {"(": ")", "[": "]", "{": "}"}


def repository_file_language(path: str | Path) -> str | None:
    """Return the indexed language for source files and shipped JSON schemas."""
    candidate = Path(path)
    if candidate.name.lower().endswith(JSON_SCHEMA_SUFFIX):
        return "JSON Schema"
    return LANGUAGE_BY_SUFFIX.get(candidate.suffix.lower())


def _rust_declaration_source_lines(path: Path) -> Iterable[tuple[int, str]]:
    """Yield Rust declaration lines outside strings and nested comments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return

    block_comment_depth = 0
    quoted_string = False
    rust_raw_terminator: str | None = None
    macro_pending = False
    macro_delimiters: list[str] = []
    for line_number, raw_line in enumerate(lines, start=1):
        code: list[str] = []
        index = 0
        while index < len(raw_line):
            if rust_raw_terminator is not None:
                raw_end = raw_line.find(rust_raw_terminator, index)
                if raw_end < 0:
                    break
                index = raw_end + len(rust_raw_terminator)
                rust_raw_terminator = None
                continue
            if quoted_string:
                if raw_line[index] == '"' and not _character_is_escaped(
                    raw_line,
                    index,
                ):
                    quoted_string = False
                index += 1
                continue
            if block_comment_depth:
                if raw_line.startswith("/*", index):
                    block_comment_depth += 1
                    index += 2
                    continue
                if raw_line.startswith("*/", index):
                    block_comment_depth -= 1
                    index += 2
                    continue
                index += 1
                continue
            if raw_line.startswith("//", index):
                break
            if raw_line.startswith("/*", index):
                block_comment_depth = 1
                index += 2
                continue
            raw_terminator = _rust_raw_string_terminator(raw_line, index)
            if raw_terminator is not None:
                code.extend("value")
                rust_raw_terminator, index = raw_terminator
                continue
            if raw_line[index] == "'":
                char_literal = RUST_CHAR_LITERAL.match(raw_line, index)
                if char_literal is not None:
                    code.extend("value")
                    index = char_literal.end()
                    continue
            if raw_line[index] == '"':
                code.extend("value")
                quoted_string = True
                index += 1
                continue
            code.append(raw_line[index])
            index += 1
        line = "".join(code)

        line, macro_pending, macro_delimiters = _without_rust_macro_tokens(
            line,
            macro_pending,
            macro_delimiters,
        )
        if line.strip():
            yield line_number, line


def _character_is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and value[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _without_rust_macro_tokens(
    line: str,
    macro_pending: bool,
    macro_delimiters: list[str],
) -> tuple[str, bool, list[str]]:
    visible: list[str] = []
    index = 0
    while index < len(line):
        if macro_pending:
            if line[index] in RUST_DELIMITER_PAIRS:
                macro_pending = False
                macro_delimiters.append(line[index])
            index += 1
            continue
        if macro_delimiters:
            if line[index] in RUST_DELIMITER_PAIRS:
                macro_delimiters.append(line[index])
            elif line[index] == RUST_DELIMITER_PAIRS[macro_delimiters[-1]]:
                macro_delimiters.pop()
            index += 1
            continue
        macro_match = RUST_MACRO_INVOCATION.search(line, index)
        if macro_match is None:
            visible.append(line[index:])
            break
        visible.append(line[index : macro_match.start()])
        macro_pending = True
        index = macro_match.end()
    return "".join(visible), macro_pending, macro_delimiters


def _rust_raw_string_terminator(
    value: str,
    index: int,
) -> tuple[str, int] | None:
    if index and (value[index - 1].isalnum() or value[index - 1] == "_"):
        return None
    prefix_length = (
        2
        if value.startswith(("br", "cr"), index)
        else 1
    )
    if not (
        value.startswith("r", index)
        or value.startswith("br", index)
        or value.startswith("cr", index)
    ):
        return None
    cursor = index + prefix_length
    while cursor < len(value) and value[cursor] == "#":
        cursor += 1
    if cursor >= len(value) or value[cursor] != '"':
        return None
    hashes = value[index + prefix_length : cursor]
    return f'"{hashes}', cursor + 1


def _rust_declaration_symbols(path: Path) -> list[SymbolRecord]:
    symbols: list[SymbolRecord] = []
    for line_number, line in _rust_declaration_source_lines(path):
        for pattern in (RUST_FUNCTION_DECLARATION, RUST_TYPE_DECLARATION):
            match = pattern.match(line)
            if match is None:
                continue
            declaration_kind = match.groupdict().get("kind")
            symbols.append(
                SymbolRecord(
                    name=match.group("name"),
                    kind=(
                        "function"
                        if pattern is RUST_FUNCTION_DECLARATION
                        else declaration_kind or "class"
                    ),
                    line=line_number,
                )
            )
            break
    return symbols


@dataclass(frozen=True)
class _ModuleBinding:
    kind: str
    target: str
    target_symbol: str
    function_local: bool = False


@dataclass(frozen=True)
class _PendingImportUse:
    caller: str | None
    local_name: str
    target: str
    target_symbol: str
    function_local: bool = False


@dataclass
class _PythonMetadata:
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    symbol_calls: dict[str, list[str]] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    resolved_calls: list[ResolvedCall] = field(default_factory=list)
    qualified_external_calls: list[QualifiedExternalCall] = field(
        default_factory=list
    )
    pending_import_calls: list[_PendingImportUse] = field(default_factory=list)
    pending_import_references: list[_PendingImportUse] = field(default_factory=list)
    module_import_bindings: list[_PendingImportUse] = field(default_factory=list)


def _import_from_bindings(
    node: ast.ImportFrom,
) -> list[tuple[str, _ModuleBinding]]:
    prefix = f"{'.' * node.level}{node.module or ''}"
    bindings: list[tuple[str, _ModuleBinding]] = []
    for alias in node.names:
        if alias.name == "*":
            continue
        target = (
            f"{prefix}.{alias.name}"
            if node.module
            else f"{'.' * node.level}{alias.name}"
        )
        bindings.append(
            (
                alias.asname or alias.name,
                _ModuleBinding(
                    kind="import",
                    target=target,
                    target_symbol=alias.name,
                ),
            )
        )
    return bindings


def _definition_time_nodes(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    nodes: list[ast.AST] = [*node.decorator_list]
    nodes.extend(getattr(node, "type_params", []))
    if isinstance(node, ast.ClassDef):
        nodes.extend(node.bases)
        nodes.extend(keyword.value for keyword in node.keywords)
        return nodes

    arguments = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    nodes.extend(
        argument.annotation
        for argument in arguments
        if argument.annotation is not None
    )
    nodes.extend(node.args.defaults)
    nodes.extend(
        default
        for default in node.args.kw_defaults
        if default is not None
    )
    if node.returns is not None:
        nodes.append(node.returns)
    return nodes


class _UnsafeModuleBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        for definition_node in _definition_time_nodes(node):
            self.visit(definition_node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        for definition_node in _definition_time_nodes(node):
            self.visit(definition_node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        for definition_node in _definition_time_nodes(node):
            self.visit(definition_node)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".", maxsplit=1)[0]
            for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name != "*"
        )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.names.update(node.names)


def _global_mutations(root: symtable.SymbolTable) -> set[str]:
    mutations: set[str] = set()

    def visit(table: symtable.SymbolTable) -> None:
        if table.get_type() != "module":
            mutations.update(
                symbol.get_name()
                for symbol in table.get_symbols()
                if symbol.is_declared_global() and symbol.is_assigned()
            )
        for child in table.get_children():
            visit(child)

    visit(root)
    return mutations


def _module_bindings(
    tree: ast.Module,
    root_table: symtable.SymbolTable,
) -> dict[str, _ModuleBinding]:
    candidates: dict[str, list[_ModuleBinding]] = {}
    unsafe_names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.setdefault(statement.name, []).append(
                _ModuleBinding(
                    kind="local",
                    target=statement.name,
                    target_symbol=statement.name,
                )
            )
            collector = _UnsafeModuleBindingCollector()
            for definition_node in _definition_time_nodes(statement):
                collector.visit(definition_node)
            unsafe_names.update(collector.names - {statement.name})
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = (
                    alias.asname
                    or alias.name.split(".", maxsplit=1)[0]
                )
                target = (
                    alias.name
                    if alias.asname
                    else alias.name.split(".", maxsplit=1)[0]
                )
                candidates.setdefault(local_name, []).append(
                    _ModuleBinding(
                        kind="module",
                        target=target,
                        target_symbol=target,
                    )
                )
        elif isinstance(statement, ast.ImportFrom):
            for local_name, binding in _import_from_bindings(statement):
                candidates.setdefault(local_name, []).append(binding)
        else:
            collector = _UnsafeModuleBindingCollector()
            collector.visit(statement)
            unsafe_names.update(collector.names)
    globally_mutated = _global_mutations(root_table)
    return {
        name: values[0]
        for name, values in candidates.items()
        if (
            len(values) == 1
            and name not in unsafe_names
            and name not in globally_mutated
        )
    }


def _leading_function_import_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, _ModuleBinding]:
    """Return unshadowed ``from`` imports from a function's leading block.

    Function-local imports are common for breaking import cycles. Restricting
    inference to the leading import block keeps the binding independent of
    control flow and statement ordering. Names rebound anywhere else in the
    function are rejected conservatively.
    """

    arguments = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    unsafe_names = {argument.arg for argument in arguments}

    statements = list(node.body)
    start = 0
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        start = 1

    candidates: dict[str, list[_ModuleBinding]] = {}
    import_indexes: set[int] = set()
    for index in range(start, len(statements)):
        statement = statements[index]
        if isinstance(statement, ast.Import):
            import_indexes.add(index)
            unsafe_names.update(
                alias.asname or alias.name.split(".", maxsplit=1)[0]
                for alias in statement.names
            )
            continue
        if not isinstance(statement, ast.ImportFrom):
            break
        import_indexes.add(index)
        for local_name, binding in _import_from_bindings(statement):
            candidates.setdefault(local_name, []).append(
                _ModuleBinding(
                    kind=binding.kind,
                    target=binding.target,
                    target_symbol=binding.target_symbol,
                    function_local=True,
                )
            )

    collector = _UnsafeModuleBindingCollector()
    for index, statement in enumerate(statements):
        if index not in import_indexes:
            collector.visit(statement)
    unsafe_names.update(collector.names)
    return {
        name: values[0]
        for name, values in candidates.items()
        if len(values) == 1 and name not in unsafe_names
    }


def _symbol_tables(
    root: symtable.SymbolTable,
) -> dict[tuple[str, str, int], symtable.SymbolTable]:
    tables: dict[tuple[str, str, int], symtable.SymbolTable] = {}

    def visit(table: symtable.SymbolTable) -> None:
        tables[(table.get_type(), table.get_name(), table.get_lineno())] = table
        for child in table.get_children():
            visit(child)

    visit(root)
    return tables


def _resolved_module_binding(
    table: symtable.SymbolTable,
    name: str,
    module_bindings: dict[str, _ModuleBinding],
    function_bindings: dict[str, _ModuleBinding] | None = None,
) -> _ModuleBinding | None:
    if table.get_type() != "module":
        try:
            symbol = table.lookup(name)
        except KeyError:
            return None
        if (
            function_bindings
            and name in function_bindings
            and symbol.is_imported()
            and symbol.is_local()
            and not symbol.is_declared_global()
            and not symbol.is_nonlocal()
        ):
            return function_bindings[name]
        if (
            symbol.is_local()
            or symbol.is_parameter()
            or symbol.is_imported()
            or symbol.is_free()
            or symbol.is_nonlocal()
            or symbol.is_declared_global()
            or not symbol.is_global()
        ):
            return None
    return module_bindings.get(name)


class _ResolvedNameUseCollector(ast.NodeVisitor):
    def __init__(
        self,
        table: symtable.SymbolTable,
        module_bindings: dict[str, _ModuleBinding],
        caller: str | None,
        function_bindings: dict[str, _ModuleBinding] | None = None,
    ) -> None:
        self.table = table
        self.module_bindings = module_bindings
        self.function_bindings = function_bindings
        self.caller = caller
        self.calls: set[tuple[str, _ModuleBinding]] = set()
        self.qualified_external_calls: set[str] = set()
        self.import_references: set[tuple[str, _ModuleBinding]] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            binding = _resolved_module_binding(
                self.table,
                node.func.id,
                self.module_bindings,
                self.function_bindings,
            )
            if binding is not None and binding.kind != "module":
                self.calls.add((node.func.id, binding))
        elif isinstance(node.func, ast.Attribute):
            attributes: list[str] = []
            current: ast.expr = node.func
            while isinstance(current, ast.Attribute):
                attributes.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                binding = _resolved_module_binding(
                    self.table,
                    current.id,
                    self.module_bindings,
                    self.function_bindings,
                )
                if binding is not None and binding.kind == "module":
                    target = ".".join(
                        [binding.target, *reversed(attributes)]
                    )
                    self.qualified_external_calls.add(target)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            binding = _resolved_module_binding(
                self.table,
                node.id,
                self.module_bindings,
                self.function_bindings,
            )
            if binding is not None and binding.kind == "import":
                self.import_references.add((node.id, binding))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return


class _FunctionCallCollector(ast.NodeVisitor):
    """Collect legacy terminal names and receiver-free direct calls separately."""

    def __init__(self) -> None:
        self.calls: set[str] = set()
        self.name_calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
            self.name_calls.add(node.func.id)
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


def _is_overload_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    module_bindings: dict[str, _ModuleBinding],
) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            binding = module_bindings.get(target.id)
            if binding is not None and binding.kind == "import" and binding.target in {
                "typing.overload",
                "typing_extensions.overload",
            }:
                return True
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "overload"
            and isinstance(target.value, ast.Name)
        ):
            binding = module_bindings.get(target.value.id)
            if binding is not None and binding.kind == "module" and binding.target in {
                "typing",
                "typing_extensions",
            }:
                return True
    return False


def _python_metadata(
    path: Path,
    relative_path: str,
) -> _PythonMetadata:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_table = symtable.symtable(source, str(path), "exec")
    except (SyntaxError, UnicodeDecodeError, OSError):
        return _PythonMetadata()
    metadata = _PythonMetadata()
    legacy_symbol_calls: dict[str, set[str]] = {}
    resolved_calls: set[tuple[str | None, str, str, str]] = set()
    qualified_external_calls: set[tuple[str | None, str]] = set()
    pending_import_calls: set[_PendingImportUse] = set()
    pending_import_references: set[_PendingImportUse] = set()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    module_bindings = _module_bindings(tree, root_table)
    metadata.module_import_bindings = [
        _PendingImportUse(
            caller=None,
            local_name=local_name,
            target=binding.target,
            target_symbol=binding.target_symbol,
        )
        for local_name, binding in module_bindings.items()
        if binding.kind == "import"
    ]
    tables = _symbol_tables(root_table)
    function_definitions: Counter[str] = Counter()
    non_overload_definitions: Counter[str] = Counter()
    for function_node in ast.walk(tree):
        if not isinstance(
            function_node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        identity = _qualified_symbol_name(function_node, parents)
        function_definitions[identity] += 1
        if not _is_overload_definition(function_node, module_bindings):
            non_overload_definitions[identity] += 1
    safe_qualified_callers = {
        identity
        for identity, count in function_definitions.items()
        if count == 1 or non_overload_definitions[identity] == 1
    }

    def collect_resolved_uses(collector: _ResolvedNameUseCollector) -> None:
        qualified_external_calls.update(
            (collector.caller, target)
            for target in collector.qualified_external_calls
        )
        for local_name, binding in collector.calls:
            if binding.kind == "local":
                resolved_calls.add(
                    (
                        collector.caller,
                        local_name,
                        relative_path,
                        binding.target_symbol,
                    )
                )
            else:
                pending_import_calls.add(
                    _PendingImportUse(
                        caller=collector.caller,
                        local_name=local_name,
                        target=binding.target,
                        target_symbol=binding.target_symbol,
                        function_local=binding.function_local,
                    )
                )
        for local_name, binding in collector.import_references:
            if binding.function_local:
                continue
            pending_import_references.add(
                _PendingImportUse(
                    caller=collector.caller,
                    local_name=local_name,
                    target=binding.target,
                    target_symbol=binding.target_symbol,
                    function_local=binding.function_local,
                )
            )

    module_collector = _ResolvedNameUseCollector(
        root_table,
        module_bindings,
        caller=None,
    )
    module_collector.visit(tree)
    collect_resolved_uses(module_collector)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified_name = _qualified_symbol_name(node, parents)
            metadata.symbols.append(
                SymbolRecord(
                    name=node.name,
                    qualified_name=qualified_name,
                    kind="function",
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", None),
                    docstring=ast.get_docstring(node),
                )
            )
            collector = _FunctionCallCollector()
            for statement in node.body:
                collector.visit(statement)
            legacy_symbol_calls.setdefault(node.name, set()).update(collector.calls)

            table = tables.get(("function", node.name, node.lineno))
            if table is not None:
                resolved_collector = _ResolvedNameUseCollector(
                    table,
                    module_bindings,
                    caller=qualified_name,
                    function_bindings=_leading_function_import_bindings(node),
                )
                for statement in node.body:
                    resolved_collector.visit(statement)
                collect_resolved_uses(resolved_collector)
        elif isinstance(node, ast.ClassDef):
            metadata.symbols.append(
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
            metadata.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = f"{'.' * node.level}{node.module or ''}"
            if prefix:
                metadata.imports.append(prefix)
            for alias in node.names:
                candidate = (
                    f"{prefix}.{alias.name}"
                    if node.module
                    else f"{'.' * node.level}{alias.name}"
                )
                metadata.imports.append(candidate)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                metadata.calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                metadata.calls.append(node.func.attr)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            metadata.references.append(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            metadata.references.append(node.attr)

    metadata.symbols.sort(key=lambda item: item.line)
    metadata.imports = sorted(set(metadata.imports))
    metadata.calls = sorted(set(metadata.calls))
    metadata.references = sorted(set(metadata.references))
    identity_counts = Counter(
        symbol.qualified_name or symbol.name
        for symbol in metadata.symbols
        if symbol.kind == "function"
    )
    unambiguous_callers = {
        identity
        for identity, count in identity_counts.items()
        if count == 1
    }
    metadata.symbol_calls = {
        symbol: sorted(called)
        for symbol, called in sorted(legacy_symbol_calls.items())
        if called
    }
    metadata.resolved_calls = [
        ResolvedCall(
            caller=caller,
            local_name=local_name,
            target_file=target_file,
            target_symbol=target_symbol,
        )
        for caller, local_name, target_file, target_symbol in sorted(
            resolved_calls,
            key=lambda item: (item[0] or "", *item[1:]),
        )
        if caller is None or caller in unambiguous_callers
    ]
    metadata.qualified_external_calls = [
        QualifiedExternalCall(caller=caller, target=target)
        for caller, target in sorted(
            qualified_external_calls,
            key=lambda item: (item[0] or "", item[1]),
        )
        if caller is None or caller in safe_qualified_callers
    ]
    metadata.pending_import_calls = sorted(
        (
            use
            for use in pending_import_calls
            if use.caller is None or use.caller in unambiguous_callers
        ),
        key=lambda item: (
            item.caller or "",
            item.local_name,
            item.target,
            item.target_symbol,
            item.function_local,
        ),
    )
    metadata.pending_import_references = sorted(
        (
            use
            for use in pending_import_references
            if use.caller is None or use.caller in unambiguous_callers
        ),
        key=lambda item: (
            item.caller or "",
            item.local_name,
            item.target,
            item.target_symbol,
            item.function_local,
        ),
    )
    return metadata


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


def repository_map_input_files(
    root: Path,
    included_files: Iterable[str],
) -> list[str]:
    root = root.resolve()
    return [
        str(relative)
        for path, relative in _repository_files(root, included_files)
        if repository_file_language(path) is not None
    ]


def _python_source_roots(files: Iterable[FileRecord]) -> frozenset[str]:
    python_paths = [
        Path(file.path).parts
        for file in files
        if file.language == "Python"
    ]
    return frozenset(
        root
        for root in {"src", "lib"}
        if any(len(parts) > 1 and parts[0] == root for parts in python_paths)
        and not any(parts == (root, "__init__.py") for parts in python_paths)
    )


def _python_module(
    relative_path: str,
    source_roots: frozenset[str],
) -> tuple[str, str]:
    relative = Path(relative_path)
    parts = list(relative.with_suffix("").parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    if parts and len(parts) > 1 and parts[0] in source_roots:
        parts.pop(0)
    module = ".".join(parts)
    package = module if is_package else module.rpartition(".")[0]
    return module, package


def _resolve_python_import(
    imported: str,
    package: str,
    module_paths: dict[str, set[str]],
) -> tuple[set[str], str | None]:
    level = len(imported) - len(imported.lstrip("."))
    suffix = imported[level:]
    if level:
        package_parts = package.split(".") if package else []
        parent_count = max(0, level - 1)
        if parent_count > len(package_parts):
            return set(), None
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
            symbol_suffix = candidate.removeprefix(module_candidate).lstrip(".")
            symbol = (
                symbol_suffix.split(".", maxsplit=1)[0]
                if symbol_suffix
                else None
            )
            return set(paths), symbol
        parts.pop()
    return set(), None


def _resolve_pending_import(
    use: _PendingImportUse,
    package: str,
    module_paths: dict[str, set[str]],
    files_by_path: dict[str, FileRecord],
) -> tuple[str, str] | None:
    target_paths, target_symbol = _resolve_python_import(
        use.target,
        package,
        module_paths,
    )
    if (
        len(target_paths) != 1
        or target_symbol is None
        or target_symbol != use.target_symbol
    ):
        return None
    target_path = next(iter(target_paths))
    target_file = files_by_path.get(target_path)
    if target_file is None:
        return None
    targets = [
        symbol
        for symbol in target_file.symbols
        if symbol.qualified_name == target_symbol
    ]
    if len(targets) != 1:
        return None
    return target_path, targets[0].qualified_name or targets[0].name


def _resolve_python_local_imports(
    files: list[FileRecord],
    pending_calls: dict[str, list[_PendingImportUse]],
    pending_references: dict[str, list[_PendingImportUse]],
    module_import_bindings: dict[str, list[_PendingImportUse]],
) -> None:
    module_paths: dict[str, set[str]] = {}
    module_by_path: dict[str, tuple[str, str]] = {}
    files_by_path = {file.path: file for file in files}
    source_roots = _python_source_roots(files)
    for file in files:
        if file.language != "Python":
            continue
        module, package = _python_module(file.path, source_roots)
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
            paths, symbol = _resolve_python_import(
                imported,
                package,
                module_paths,
            )
            resolved.update(paths)
            if symbol:
                for resolved_path in paths:
                    imported_symbols.setdefault(resolved_path, set()).add(symbol)
        file.local_imports = sorted(resolved - {file.path})
        file.local_import_symbols = {
            path: sorted(symbols)
            for path, symbols in sorted(imported_symbols.items())
            if path != file.path
        }
        safe_import_symbols: dict[str, set[str]] = {}
        for binding in module_import_bindings.get(file.path, []):
            target_paths, target_symbol = _resolve_python_import(
                binding.target,
                package,
                module_paths,
            )
            if (
                len(target_paths) != 1
                or target_symbol is None
                or target_symbol != binding.target_symbol
            ):
                continue
            target_path = next(iter(target_paths))
            if target_path != file.path:
                safe_import_symbols.setdefault(target_path, set()).add(
                    target_symbol
                )
        file.module_import_symbols = {
            path: sorted(symbols)
            for path, symbols in sorted(safe_import_symbols.items())
        }

        resolved_call_keys = {
            (
                call.caller,
                call.local_name,
                call.target_file,
                call.target_symbol,
            )
            for call in file.resolved_calls
        }
        function_local_call_keys = {
            (
                call.caller,
                call.local_name,
                call.target_file,
                call.target_symbol,
            )
            for call in file.function_local_import_calls
        }
        resolved_reference_symbols: dict[str, set[str]] = {}

        for use in pending_calls.get(file.path, []):
            target = _resolve_pending_import(
                use,
                package,
                module_paths,
                files_by_path,
            )
            if target is None:
                continue
            target_path, target_symbol = target
            resolved_call_keys.add(
                (
                    use.caller,
                    use.local_name,
                    target_path,
                    target_symbol,
                )
            )
            if use.function_local:
                function_local_call_keys.add(
                    (
                        use.caller,
                        use.local_name,
                        target_path,
                        target_symbol,
                    )
                )
        for use in pending_references.get(file.path, []):
            target = _resolve_pending_import(
                use,
                package,
                module_paths,
                files_by_path,
            )
            if target is None:
                continue
            target_path, target_symbol = target
            if target_path != file.path:
                resolved_reference_symbols.setdefault(target_path, set()).add(
                    target_symbol
                )

        file.resolved_calls = [
            ResolvedCall(
                caller=caller,
                local_name=local_name,
                target_file=target_file,
                target_symbol=target_symbol,
            )
            for caller, local_name, target_file, target_symbol in sorted(
                resolved_call_keys,
                key=lambda item: (item[0] or "", *item[1:]),
            )
        ]
        file.function_local_import_calls = [
            ResolvedCall(
                caller=caller,
                local_name=local_name,
                target_file=target_file,
                target_symbol=target_symbol,
            )
            for caller, local_name, target_file, target_symbol in sorted(
                function_local_call_keys,
                key=lambda item: (item[0] or "", *item[1:]),
            )
        ]
        file.name_calls = sorted({call.local_name for call in file.resolved_calls})
        qualified_calls: dict[str, set[str]] = {}
        for call in file.resolved_calls:
            if call.caller is not None:
                qualified_calls.setdefault(call.caller, set()).add(
                    call.target_symbol
                )
        file.qualified_symbol_calls = {
            caller: sorted(targets)
            for caller, targets in sorted(qualified_calls.items())
        }
        file.resolved_import_references = {
            path: sorted(symbols)
            for path, symbols in sorted(resolved_reference_symbols.items())
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
    pending_calls: dict[str, list[_PendingImportUse]] = {}
    pending_references: dict[str, list[_PendingImportUse]] = {}
    module_import_bindings: dict[str, list[_PendingImportUse]] = {}
    for path, relative in _repository_files(root, included_files):
        filename = path.name
        for index, part in enumerate(relative.parts[:-1]):
            if part in {"test", "tests"}:
                test_directories.add(str(Path(*relative.parts[: index + 1])))
        if filename in RUNTIME_FILES:
            runtime_files.append(str(relative))
        if filename in ENTRYPOINT_NAMES:
            entrypoints.append(str(relative))
        language = repository_file_language(path)
        if not language:
            continue
        languages[language] += 1
        metadata = _PythonMetadata()
        if language == "Python":
            metadata = _python_metadata(path, str(relative))
            pending_calls[str(relative)] = metadata.pending_import_calls
            pending_references[str(relative)] = (
                metadata.pending_import_references
            )
            module_import_bindings[str(relative)] = (
                metadata.module_import_bindings
            )
            for imported in metadata.imports:
                framework = FRAMEWORK_IMPORTS.get(imported.split(".", maxsplit=1)[0])
                if framework:
                    frameworks.add(framework)
        elif language == "Rust":
            metadata.symbols = _rust_declaration_symbols(path)
        files.append(
            FileRecord(
                path=str(relative),
                language=language,
                symbols=metadata.symbols,
                imports=metadata.imports,
                calls=metadata.calls,
                symbol_calls=metadata.symbol_calls,
                resolved_calls=metadata.resolved_calls,
                qualified_external_calls=metadata.qualified_external_calls,
                references=metadata.references,
                test_file="test" in filename.lower() or "tests" in relative.parts,
            )
        )
    _resolve_python_local_imports(
        files,
        pending_calls,
        pending_references,
        module_import_bindings,
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
