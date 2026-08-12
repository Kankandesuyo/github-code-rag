import ast
from pathlib import Path

from app.schemas.report_schema import FunctionCall


class _FunctionCallVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        source_file: str,
        definitions: dict[str, set[str]],
        imported_symbols: dict[str, tuple[str, str]],
        imported_modules: dict[str, str],
        module_files: dict[str, str],
    ) -> None:
        self.source_file = source_file
        self.definitions = definitions
        self.imported_symbols = imported_symbols
        self.imported_modules = imported_modules
        self.module_files = module_files
        self.scope: list[str] = []
        self.class_scope: list[str] = []
        self.calls: list[FunctionCall] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_scope.append(node.name)
        self.generic_visit(node)
        self.class_scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _caller(self) -> str:
        parts = [*self.class_scope, *self.scope]
        return ".".join(parts)

    def _append(self, node: ast.Call, callee_file: str, callee_symbol: str, call_type: str) -> None:
        caller = self._caller()
        if not caller:
            return
        self.calls.append(
            FunctionCall(
                caller_file=self.source_file,
                caller_symbol=caller,
                callee_file=callee_file,
                callee_symbol=callee_symbol,
                call_type=call_type,
                line=node.lineno,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name):
            name = function.id
            if name in self.definitions.get(self.source_file, set()):
                self._append(node, self.source_file, name, "local function")
            elif name in self.imported_symbols:
                module, symbol = self.imported_symbols[name]
                target_file = self.module_files.get(module)
                if target_file and symbol in self.definitions.get(target_file, set()):
                    self._append(node, target_file, symbol, "imported function")
        elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            base = function.value.id
            if base in {"self", "cls"} and self.class_scope:
                method = f"{self.class_scope[-1]}.{function.attr}"
                if method in self.definitions.get(self.source_file, set()):
                    self._append(node, self.source_file, method, "local method")
            else:
                module = self.imported_modules.get(base)
                target_file = self.module_files.get(module or "")
                if target_file and function.attr in self.definitions.get(target_file, set()):
                    self._append(node, target_file, function.attr, "imported module function")
        self.generic_visit(node)


class CallGraphAnalyzer:
    """Build a conservative Python call graph without importing or executing repository code."""

    def __init__(self, files: list[Path]) -> None:
        self.files = [path for path in files if path.suffix.lower() == ".py"]

    def _read_tree(self, path: Path) -> ast.AST | None:
        try:
            return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError, ValueError):
            return None

    def _module_index(self, repo_path: Path) -> dict[str, str]:
        index: dict[str, str] = {}
        for path in self.files:
            relative = path.relative_to(repo_path).as_posix()
            module = relative[:-3].replace("/", ".")
            index[module] = relative
            if module.endswith(".__init__"):
                index[module[: -len(".__init__")]] = relative
        return index

    def _definitions(self, tree: ast.AST) -> set[str]:
        definitions: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions.add(node.name)
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        definitions.add(f"{node.name}.{child.name}")
        return definitions

    def _resolve_from_module(self, source_file: str, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        source_module = source_file[:-3].replace("/", ".")
        package_parts = source_module.split(".")
        if package_parts[-1] == "__init__":
            package_parts.pop()
        else:
            package_parts.pop()
        ascend = max(node.level - 1, 0)
        if ascend:
            package_parts = package_parts[:-ascend]
        if node.module:
            package_parts.extend(node.module.split("."))
        return ".".join(package_parts)

    def _imports(self, source_file: str, tree: ast.AST) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
        symbols: dict[str, tuple[str, str]] = {}
        modules: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = self._resolve_from_module(source_file, node)
                if not module:
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        symbols[alias.asname or alias.name] = (module, alias.name)
        return symbols, modules

    def analyze(self, repo_path: Path) -> list[FunctionCall]:
        trees: dict[str, ast.AST] = {}
        definitions: dict[str, set[str]] = {}
        for path in self.files:
            relative = path.relative_to(repo_path).as_posix()
            tree = self._read_tree(path)
            if tree is not None:
                trees[relative] = tree
                definitions[relative] = self._definitions(tree)

        module_files = self._module_index(repo_path)
        calls: list[FunctionCall] = []
        seen: set[tuple[str, str, str, str, int]] = set()
        for source_file, tree in trees.items():
            imported_symbols, imported_modules = self._imports(source_file, tree)
            visitor = _FunctionCallVisitor(
                source_file=source_file,
                definitions=definitions,
                imported_symbols=imported_symbols,
                imported_modules=imported_modules,
                module_files=module_files,
            )
            visitor.visit(tree)
            for call in visitor.calls:
                key = (
                    call.caller_file,
                    call.caller_symbol,
                    call.callee_file,
                    call.callee_symbol,
                    call.line,
                )
                if key not in seen:
                    seen.add(key)
                    calls.append(call)
        return calls[:240]
