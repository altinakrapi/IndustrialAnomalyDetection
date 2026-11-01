import ast
import re
from pathlib import Path

from app.main import app

ROLES = {"administrator", "data_analyst", "maintenance_technician"}
PUBLIC_AUTH_ROUTES = {
    ("POST", "/api/v1/auth/register"),
    ("GET", "/api/v1/auth/status"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
}


def _function_definitions() -> dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    root = Path(__file__).resolve().parents[1]
    found: dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for path in (root / "app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(
                    ast.unparse(decorator).startswith(("router.", "key_router."))
                    for decorator in node.decorator_list
                ):
                    relative = path.relative_to(root)
                    existing = found.get(node.name)
                    if existing is None or (
                        "api" in relative.parts and "api" not in existing[0].parts
                    ):
                        found[node.name] = (relative, node)
    return found


def _dependency_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    dependencies: list[str] = []
    for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        default_index = (
            len([*node.args.posonlyargs, *node.args.args])
            - len(node.args.defaults)
        )
        if argument.annotation and argument.arg == "self":
            continue
        for item in ast.walk(argument):
            pass
        default = None
        positional = [*node.args.posonlyargs, *node.args.args]
        if argument in positional and positional.index(argument) >= default_index:
            default = node.args.defaults[positional.index(argument) - default_index]
        if argument in node.args.kwonlyargs:
            kw_index = node.args.kwonlyargs.index(argument)
            if kw_index < len(node.args.kw_defaults) and node.args.kw_defaults[kw_index]:
                default = node.args.kw_defaults[kw_index]
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name):
            if default.func.id == "Depends":
                if default.args and isinstance(default.args[0], ast.Name):
                    dependencies.append(default.args[0].id)
                elif default.args and isinstance(default.args[0], ast.Call):
                    dependencies.append(ast.unparse(default.args[0]))
    return dependencies


def _roles_from_dependencies(
    dependencies: list[str], constants: dict[str, set[str]]
) -> str:
    roles: set[str] = set()
    for dependency in dependencies:
        if dependency in constants:
            roles.update(constants[dependency])
        match = re.search(
            r'require_roles\(([^)]*)\)', dependency
        )
        if match:
            roles.update(re.findall(r'"([^"]+)"', match.group(1)))
    if not roles:
        return "all authenticated"
    return ", ".join(sorted(roles))


def _constant_role_sets(path: Path) -> dict[str, set[str]]:
    root = Path(__file__).resolve().parents[1]
    result: dict[str, set[str]] = {}
    for source in (root / "app").rglob("*.py"):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "require_roles":
                continue
            names = {
                arg.value for arg in node.value.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            }
            if names and names <= ROLES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        result[target.id] = names
    return result


def generate() -> str:
    definitions = _function_definitions()
    constants = _constant_role_sets(Path(__file__).resolve().parents[1])
    openapi = app.openapi()
    rows: list[str] = []
    for route, methods in sorted(openapi["paths"].items()):
        for method, operation in methods.items():
            method = method.upper()
            if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                continue
            operation_id = operation["operationId"]
            function_name = operation_id.split("_api_v1_", 1)[0]
            found = definitions.get(function_name)
            dependencies: list[str] = []
            location = "public or application-defined dependency"
            if found:
                source, node = found
                dependencies = _dependency_names(node)
                location = str(source)
            roles = "public" if (method, route) in PUBLIC_AUTH_ROUTES else _roles_from_dependencies(dependencies, constants)
            rows.append(
                f"| {method} | `{route}` | {roles} | `{location}` |"
            )

    header = (
        "# Permission matrix\n\n"
        "Generated automatically from FastAPI OpenAPI and route-function dependency declarations. "
        "Authentication routes explicitly listed as public are outside RBAC; protected application routes "
        "require authentication and/or role dependencies.\n\n"
        "| Method | Route | Roles allowed | Permission check |\n"
        "|---|---|---|---|\n"
    )
    return header + "\n".join(rows) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root.parent / "PERMISSIONS.md"
    output.write_text(generate(), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
