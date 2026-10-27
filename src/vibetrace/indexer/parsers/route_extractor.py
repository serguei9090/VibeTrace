"""Route pattern extractor for Python (FastAPI/Flask) and JS/TS (Express/Next.js)."""

from __future__ import annotations

import re
from typing import Any

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode

# Matches @app.get("/path"), @router.post('/path', ...), @api_router.delete(...)
FASTAPI_ROUTE_RE = re.compile(
    r"@(?:\w+\.)?(\w+)\.(get|post|put|delete|patch|options|head)\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# Matches @app.route("/path", methods=["GET", "POST"])
FLASK_ROUTE_RE = re.compile(
    r"@(?:\w+\.)?(app|bp|blueprint|\w+)\.route\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
FLASK_METHODS_RE = re.compile(r"methods\s*=\s*[\[\(](.*?)[\]\)]", re.IGNORECASE | re.DOTALL)

# Matches APIRouter(prefix="/...") and Blueprint(..., url_prefix="/...")
API_ROUTER_RE = re.compile(
    r"(\w+)(?:\s*:\s*[\w\.]+)?\s*=\s*(?:fastapi\.)?APIRouter\s*\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
BLUEPRINT_RE = re.compile(
    r"(\w+)(?:\s*:\s*[\w\.]+)?\s*=\s*(?:flask\.)?Blueprint\s*\([^)]*url_prefix\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# Matches @router.api_route("/path", methods=["GET", "POST"]) or @app.api_route("/path")
API_ROUTE_RE = re.compile(
    r"@(?:\w+\.)?(\w+)\.api_route\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def extract_python_router_prefixes(source: str | bytes) -> dict[str, str]:
    """Extract router/blueprint variable prefixes from Python code (case-insensitive keys)."""
    text = source.decode("utf-8", errors="replace") if isinstance(source, bytes) else source
    prefixes: dict[str, str] = {}
    for var, prefix in API_ROUTER_RE.findall(text):
        prefixes[var.lower()] = prefix
    for var, prefix in BLUEPRINT_RE.findall(text):
        prefixes[var.lower()] = prefix
    return prefixes


def extract_python_routes(
    decorators: list[str] | None,
    handler_node: DBNode,
    file_path: str,
    node_id_fn: Any,
    router_prefixes: dict[str, str] | None = None,
) -> tuple[list[DBNode], list[Edge]]:
    """Extract synthetic Endpoint nodes and 'routes' edges from Python route decorators."""
    if not decorators:
        return [], []

    endpoint_nodes: list[DBNode] = []
    route_edges: list[Edge] = []

    for dec in decorators:
        # 1. FastAPI / Starlette
        m_fastapi = FASTAPI_ROUTE_RE.search(dec)
        if m_fastapi:
            router_var = m_fastapi.group(1).lower()
            method = m_fastapi.group(2).upper()
            route_path = m_fastapi.group(3)

            if router_prefixes and router_var in router_prefixes:
                pfx = router_prefixes[router_var].rstrip("/")
                sub_path = route_path.lstrip("/")
                route_path = f"{pfx}/{sub_path}" if pfx else f"/{sub_path}"

            endpoint_name = f"{method} {route_path}"
            endpoint_id = node_id_fn("__endpoint__", endpoint_name, kind="endpoint")

            node = DBNode(
                id=endpoint_id,
                name=endpoint_name,
                qualified_name=endpoint_name,
                parent_id=handler_node.parent_id,
                kind="endpoint",
                file_path=file_path,
                start_line=handler_node.start_line,
                end_line=handler_node.start_line,
                signature=f"{method} {route_path}",
                docstring=handler_node.docstring,
                decorators=[dec],
                visibility="public",
            )
            edge = Edge(
                from_node_id=endpoint_id,
                to_node_id=handler_node.id,
                relation_type="routes",
                call_line=handler_node.start_line,
            )
            endpoint_nodes.append(node)
            route_edges.append(edge)
            continue

        # 2. FastAPI generic api_route: @router.api_route("/path", methods=["GET", "POST"])
        m_api_route = API_ROUTE_RE.search(dec)
        if m_api_route:
            router_var = m_api_route.group(1).lower()
            route_path = m_api_route.group(2)
            if router_prefixes and router_var in router_prefixes:
                pfx = router_prefixes[router_var].rstrip("/")
                sub_path = route_path.lstrip("/")
                route_path = f"{pfx}/{sub_path}" if pfx else f"/{sub_path}"
            methods = ["GET"]
            m_methods = FLASK_METHODS_RE.search(dec)
            if m_methods:
                parsed_methods = re.findall(r"[\"']([A-Za-z]+)[\"']", m_methods.group(1))
                if parsed_methods:
                    methods = [m.upper() for m in parsed_methods]

            for method in methods:
                endpoint_name = f"{method} {route_path}"
                endpoint_id = node_id_fn("__endpoint__", endpoint_name, kind="endpoint")
                node = DBNode(
                    id=endpoint_id,
                    name=endpoint_name,
                    qualified_name=endpoint_name,
                    parent_id=handler_node.parent_id,
                    kind="endpoint",
                    file_path=file_path,
                    start_line=handler_node.start_line,
                    end_line=handler_node.start_line,
                    signature=f"{method} {route_path}",
                    docstring=handler_node.docstring,
                    decorators=[dec],
                    visibility="public",
                )
                edge = Edge(
                    from_node_id=endpoint_id,
                    to_node_id=handler_node.id,
                    relation_type="routes",
                    call_line=handler_node.start_line,
                )
                endpoint_nodes.append(node)
                route_edges.append(edge)
            continue

        # 3. Flask
        m_flask = FLASK_ROUTE_RE.search(dec)
        if m_flask:
            bp_var = m_flask.group(1).lower()
            route_path = m_flask.group(2)
            if router_prefixes and bp_var in router_prefixes:
                pfx = router_prefixes[bp_var].rstrip("/")
                sub_path = route_path.lstrip("/")
                route_path = f"{pfx}/{sub_path}" if pfx else f"/{sub_path}"
            methods = ["GET"]
            m_methods = FLASK_METHODS_RE.search(dec)
            if m_methods:
                parsed_methods = re.findall(r"[\"']([A-Za-z]+)[\"']", m_methods.group(1))
                if parsed_methods:
                    methods = [m.upper() for m in parsed_methods]

            for method in methods:
                endpoint_name = f"{method} {route_path}"
                endpoint_id = node_id_fn("__endpoint__", endpoint_name, kind="endpoint")

                node = DBNode(
                    id=endpoint_id,
                    name=endpoint_name,
                    qualified_name=endpoint_name,
                    parent_id=handler_node.parent_id,
                    kind="endpoint",
                    file_path=file_path,
                    start_line=handler_node.start_line,
                    end_line=handler_node.start_line,
                    signature=f"{method} {route_path}",
                    docstring=handler_node.docstring,
                    decorators=[dec],
                    visibility="public",
                )
                edge = Edge(
                    from_node_id=endpoint_id,
                    to_node_id=handler_node.id,
                    relation_type="routes",
                    call_line=handler_node.start_line,
                )
                endpoint_nodes.append(node)
                route_edges.append(edge)

    return endpoint_nodes, route_edges


def extract_js_endpoint_route(
    node: Any,
    called_name: str,
    args: list[Any],
    parent_id: str,
    file_path: str,
    file_module_id: str,
    get_text_fn: Any,
    get_id_fn: Any,
    create_node_fn: Any,
    create_edge_fn: Any,
    resolve_call_target_fn: Any,
    ctx: Any,
    nodes: list[DBNode],
    edges: list[Edge],
) -> None:
    """Extract clientside or backend API requests/routes in JS/TS AST."""
    if not args:
        return
    first_arg = args[0]
    if getattr(first_arg, "type", "") not in ("string", "template_string"):
        if getattr(first_arg, "type", "") == "binary_expression":
            left = getattr(first_arg, "child_by_field_name", lambda _: None)("left")
            if getattr(left, "type", "") in ("string", "template_string"):
                first_arg = left
            else:
                return
        else:
            return

    raw_url = get_text_fn(first_arg).strip("'\"`")
    if not (raw_url.startswith("/") or "/api/" in raw_url):
        return

    lower_func = called_name.lower()
    fn_part = lower_func.split(".")[-1]
    method = next(
        (
            m.upper()
            for m in ("post", "put", "delete", "patch", "options", "head", "get")
            if fn_part == m or f".{m}" in lower_func
        ),
        "GET",
    )
    if fn_part == "fetch" and len(args) > 1:
        opts_text = get_text_fn(args[1])
        if m_meth := re.search(r"""method\s*:\s*['"]([A-Za-z]+)['"]""", opts_text, re.IGNORECASE):
            method = m_meth.group(1).upper()
    endpoint_name = f"{method} {raw_url}"
    endpoint_id = get_id_fn("__endpoint__", endpoint_name, kind="endpoint")
    if not any(n.id == endpoint_id for n in nodes):
        nodes.append(
            create_node_fn(
                endpoint_id,
                endpoint_name,
                "endpoint",
                file_path,
                node,
                qualified_name=endpoint_name,
                parent_id=file_module_id,
            )
        )
    is_b_route = (
        any(called_name.startswith(p) for p in ("app.", "router.", "server.", "api."))
        and len(args) >= 2
    )
    h_arg = args[-1] if is_b_route else None
    if (
        h_arg
        and getattr(h_arg, "type", "") in ("identifier", "member_expression")
        and (h_id := resolve_call_target_fn(get_text_fn(h_arg), ctx))
    ):
        edges.append(create_edge_fn(endpoint_id, h_id, "routes", node=h_arg))
        edges.append(create_edge_fn(parent_id, endpoint_id, "defines", node=node))
    elif h_arg and getattr(h_arg, "type", "") in (
        "arrow_function",
        "function_expression",
        "function",
    ):
        h_line = h_arg.start_point[0] + 1
        h_id = get_id_fn(file_path, f"<anonymous:L{h_line}>")
        edges.append(create_edge_fn(endpoint_id, h_id, "routes", node=h_arg))
        edges.append(create_edge_fn(parent_id, endpoint_id, "defines", node=node))
    else:
        edges.append(create_edge_fn(parent_id, endpoint_id, "routes", node=node))
