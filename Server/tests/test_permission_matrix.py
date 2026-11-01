import ast
from pathlib import Path

from app.main import app
from scripts.generate_permission_matrix import generate, PUBLIC_AUTH_ROUTES


SERVER_ROOT = Path(__file__).resolve().parents[1]


def _route_functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    functions = {}
    for path in (SERVER_ROOT / "app").rglob("*.py"):
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
                    functions[node.name] = node
    return functions


def test_permission_matrix_is_generated_from_current_openapi():
    matrix = (SERVER_ROOT.parent / "PERMISSIONS.md").read_text(encoding="utf-8")
    assert generate() == matrix


def test_every_application_route_has_a_permission_dependency():
    functions = _route_functions()
    openapi = app.openapi()
    unchecked = []
    for route, operations in openapi["paths"].items():
        if not route.startswith("/api/v1/"):
            continue
        for method, operation in operations.items():
            method = method.upper()
            if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                continue
            if (method, route) in PUBLIC_AUTH_ROUTES:
                continue
            function_name = operation["operationId"].split("_api_v1_", 1)[0]
            node = functions.get(function_name)
            if node is None:
                unchecked.append(f"{method} {route}: endpoint not found")
                continue
            defaults = {
                ast.unparse(default)
                for default in [*node.args.defaults, *node.args.kw_defaults]
                if default is not None
            }
            if not any("Depends(" in default for default in defaults):
                unchecked.append(f"{method} {route}: no Depends dependency")
    assert not unchecked, "\n".join(unchecked)
