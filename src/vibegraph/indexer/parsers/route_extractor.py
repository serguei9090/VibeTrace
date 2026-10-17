"""Route pattern extractor for Python (FastAPI/Flask) and JS/TS (Express/Next.js)."""

from __future__ import annotations

import re
from typing import Any

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode

# Matches @app.get("/path"), @router.post('/path', ...), @api_router.delete(...)
FASTAPI_ROUTE_RE = re.compile(
    r"@(?:\w+\.)?(?:app|router|api_router)\.(get|post|put|delete|patch|options|head)\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# Matches @app.route("/path", methods=["GET", "POST"])
FLASK_ROUTE_RE = re.compile(
    r"@(?:\w+\.)?(?:app|bp|blueprint)\.route\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
FLASK_METHODS_RE = re.compile(r"methods\s*=\s*\[(.*?)\]", re.IGNORECASE)

# Matches Express app.get("/path", ...) or router.post('/path', ...)
EXPRESS_ROUTE_RE = re.compile(
    r"(?:app|router)\.(get|post|put|delete|patch|options|head)\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def extract_python_routes(
    decorators: list[str] | None,
    handler_node: DBNode,
    file_path: str,
    node_id_fn: Any,
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
            method = m_fastapi.group(1).upper()
            route_path = m_fastapi.group(2)
            endpoint_name = f"{method} {route_path}"
            endpoint_id = node_id_fn(file_path, endpoint_name, kind="endpoint")

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

        # 2. Flask
        m_flask = FLASK_ROUTE_RE.search(dec)
        if m_flask:
            route_path = m_flask.group(1)
            methods = ["GET"]
            m_methods = FLASK_METHODS_RE.search(dec)
            if m_methods:
                parsed_methods = re.findall(r"[\"']([A-Za-z]+)[\"']", m_methods.group(1))
                if parsed_methods:
                    methods = [m.upper() for m in parsed_methods]

            for method in methods:
                endpoint_name = f"{method} {route_path}"
                endpoint_id = node_id_fn(file_path, endpoint_name, kind="endpoint")

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
