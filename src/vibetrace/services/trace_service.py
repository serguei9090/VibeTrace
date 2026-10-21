"""Bidirectional BFS execution tracepath and surgical source code slicing service."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB


class TraceNodeInfo(BaseModel):
    """Metadata for a node along an execution tracepath."""

    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None


class TraceHop(BaseModel):
    """Single directed hop in an execution tracepath."""

    step_index: int
    from_node: TraceNodeInfo
    to_node: TraceNodeInfo
    relation_type: str
    call_line: int | None = None


class TracePathResult(BaseModel):
    """Result of a bidirectional execution tracepath search between two symbols."""

    from_symbol: str
    to_symbol: str
    found: bool
    hops: list[TraceHop] = Field(default_factory=list)
    paths: list[list[TraceHop]] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)


class CalleeContract(BaseModel):
    """Compact signature contract for a direct outgoing callee."""

    id: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int | None = None
    signature: str | None = None
    call_line: int | None = None


class CodeSliceResult(BaseModel):
    """Result of surgical source code slicing for a symbol."""

    found: bool
    node_id: str | None = None
    name: str
    qualified_name: str
    kind: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    docstring: str | None = None
    source_code: str = ""
    callees: list[CalleeContract] = Field(default_factory=list)


def _row_to_trace_node(row: dict[str, Any], node_id: str | None = None) -> TraceNodeInfo:
    nid = node_id or row.get("id") or row.get("neighbor_id") or ""
    name = row.get("name") or ""
    return TraceNodeInfo(
        id=nid,
        name=name,
        qualified_name=row.get("qualified_name") or name,
        kind=row.get("kind") or "function",
        file_path=row.get("file_path") or "",
        start_line=row.get("start_line"),
        end_line=row.get("end_line"),
        signature=row.get("signature"),
    )


def _resolve_symbol_candidates(db: IndexerDB, symbol: str) -> list[dict[str, Any]]:
    exact = db.find_nodes_by_name(symbol)
    if exact:
        return exact
    return db.search_ast_exact_or_prefix(symbol, limit=5)


def trace_execution_path(
    db: IndexerDB,
    from_symbol: str,
    to_symbol: str,
    max_depth: int = 6,
    engine: str = "bfs",
) -> TracePathResult:
    """Find shortest execution tracepaths between two symbols using BFS or recursive CTE."""
    start_rows = _resolve_symbol_candidates(db, from_symbol)
    target_rows = _resolve_symbol_candidates(db, to_symbol)

    if not start_rows or not target_rows:
        return TracePathResult(from_symbol=from_symbol, to_symbol=to_symbol, found=False)

    if from_symbol != to_symbol:
        target_ids = {r["id"] for r in target_rows}
        distinct_start = [r for r in start_rows if r["id"] not in target_ids]
        if distinct_start:
            start_rows = distinct_start
        else:
            start_ids = {r["id"] for r in start_rows}
            distinct_target = [r for r in target_rows if r["id"] not in start_ids]
            if distinct_target:
                target_rows = distinct_target
            else:
                return TracePathResult(from_symbol=from_symbol, to_symbol=to_symbol, found=False)

    if engine == "cte":
        path_nodes = db.find_path_recursive_cte(
            start_rows[0]["id"], target_rows[0]["id"], max_depth=max_depth
        )
        if not path_nodes:
            return TracePathResult(from_symbol=from_symbol, to_symbol=to_symbol, found=False)
        node_ids = [n["id"] for n in path_nodes]
        hops: list[TraceHop] = []
        with db._get_conn() as conn:
            for i in range(len(path_nodes) - 1):
                u_id = path_nodes[i]["id"]
                v_id = path_nodes[i + 1]["id"]
                row = conn.execute(
                    "SELECT relation_type, call_line FROM edges "
                    "WHERE from_node_id = ? AND to_node_id = ? LIMIT 1",
                    (u_id, v_id),
                ).fetchone()
                rel_type = row["relation_type"] if (row and row["relation_type"]) else "calls"
                c_line = row["call_line"] if row else None
                hops.append(
                    TraceHop(
                        step_index=i + 1,
                        from_node=_row_to_trace_node(path_nodes[i]),
                        to_node=_row_to_trace_node(path_nodes[i + 1]),
                        relation_type=rel_type,
                        call_line=c_line,
                    )
                )
        return TracePathResult(
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            found=True,
            hops=hops,
            paths=[hops] if hops else [],
            node_ids=node_ids,
        )

    node_map: dict[str, TraceNodeInfo] = {}
    fwd_parent: dict[str, tuple[str | None, dict[str, Any] | None]] = {}
    bwd_parent: dict[str, tuple[str | None, dict[str, Any] | None]] = {}
    fwd_depth: dict[str, int] = {}
    bwd_depth: dict[str, int] = {}

    fwd_queue: deque[str] = deque()
    bwd_queue: deque[str] = deque()

    for r in start_rows:
        nid = r["id"]
        node_map[nid] = _row_to_trace_node(r, nid)
        fwd_parent[nid] = (None, None)
        fwd_depth[nid] = 0
        fwd_queue.append(nid)

    for r in target_rows:
        nid = r["id"]
        node_map[nid] = _row_to_trace_node(r, nid)
        bwd_parent[nid] = (None, None)
        bwd_depth[nid] = 0
        bwd_queue.append(nid)

    overlap = set(fwd_depth.keys()) & set(bwd_depth.keys())
    if overlap:
        first_id = next(iter(overlap))
        return TracePathResult(
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            found=True,
            hops=[],
            paths=[],
            node_ids=[first_id],
        )

    rel_priority = {"calls": 0, "references": 1, "inherits": 2, "implements": 3, "defines": 4}
    bridges: list[tuple[str, str, dict[str, Any]]] = []

    while fwd_queue and bwd_queue:
        min_f = fwd_depth[fwd_queue[0]]
        min_b = bwd_depth[bwd_queue[0]]
        if min_f + min_b >= max_depth:
            break

        if len(fwd_queue) <= len(bwd_queue):
            level_size = len(fwd_queue)
            for _ in range(level_size):
                u = fwd_queue.popleft()
                d_u = fwd_depth[u]
                if d_u >= max_depth:
                    continue
                neighbors = sorted(
                    db.get_neighbors(u, "down"),
                    key=lambda x: rel_priority.get(x["relation_type"], 9),
                )
                for edge in neighbors:
                    v = edge["neighbor_id"]
                    node_map.setdefault(v, _row_to_trace_node(edge, v))
                    if v in bwd_depth and d_u + 1 + bwd_depth[v] <= max_depth:
                        bridges.append((u, v, edge))
                    if v not in fwd_depth:
                        fwd_depth[v] = d_u + 1
                        fwd_parent[v] = (u, edge)
                        fwd_queue.append(v)
        else:
            level_size = len(bwd_queue)
            for _ in range(level_size):
                v = bwd_queue.popleft()
                d_v = bwd_depth[v]
                if d_v >= max_depth:
                    continue
                neighbors = sorted(
                    db.get_neighbors(v, "up"),
                    key=lambda x: rel_priority.get(x["relation_type"], 9),
                )
                for edge in neighbors:
                    u = edge["neighbor_id"]
                    node_map.setdefault(u, _row_to_trace_node(edge, u))
                    if u in fwd_depth and fwd_depth[u] + 1 + d_v <= max_depth:
                        bridges.append((u, v, edge))
                    if u not in bwd_depth:
                        bwd_depth[u] = d_v + 1
                        bwd_parent[u] = (v, edge)
                        bwd_queue.append(u)

        if bridges:
            break

    if not bridges:
        return TracePathResult(from_symbol=from_symbol, to_symbol=to_symbol, found=False)

    paths: list[list[TraceHop]] = []
    seen_signatures: set[tuple[str, ...]] = set()

    for u_mid, v_mid, mid_edge in bridges:
        fwd_edges: list[tuple[str, str, dict[str, Any]]] = []
        curr = u_mid
        while True:
            prev_id, prev_edge = fwd_parent.get(curr, (None, None))
            if prev_id is None or prev_edge is None:
                break
            fwd_edges.append((prev_id, curr, prev_edge))
            curr = prev_id
        fwd_edges.reverse()

        bwd_edges: list[tuple[str, str, dict[str, Any]]] = []
        curr = v_mid
        while True:
            next_id, next_edge = bwd_parent.get(curr, (None, None))
            if next_id is None or next_edge is None:
                break
            bwd_edges.append((curr, next_id, next_edge))
            curr = next_id

        full_chain = [*fwd_edges, (u_mid, v_mid, mid_edge), *bwd_edges]
        node_seq = (full_chain[0][0], *(step[1] for step in full_chain))
        if node_seq in seen_signatures or len(node_seq) != len(set(node_seq)):
            continue
        seen_signatures.add(node_seq)

        hops: list[TraceHop] = []
        for idx, (src_id, dst_id, edge_data) in enumerate(full_chain, start=1):
            hops.append(
                TraceHop(
                    step_index=idx,
                    from_node=node_map[src_id],
                    to_node=node_map[dst_id],
                    relation_type=edge_data["relation_type"],
                    call_line=edge_data.get("call_line"),
                )
            )
        paths.append(hops)

    if not paths:
        return TracePathResult(from_symbol=from_symbol, to_symbol=to_symbol, found=False)

    paths.sort(
        key=lambda p: (
            len(p),
            sum(1 for h in p if h.relation_type == "defines"),
        )
    )
    primary = paths[0]
    unique_node_ids: list[str] = [primary[0].from_node.id, *(h.to_node.id for h in primary)]
    for path in paths[1:]:
        for h in path:
            if h.from_node.id not in unique_node_ids:
                unique_node_ids.append(h.from_node.id)
            if h.to_node.id not in unique_node_ids:
                unique_node_ids.append(h.to_node.id)

    return TracePathResult(
        from_symbol=from_symbol,
        to_symbol=to_symbol,
        found=True,
        hops=primary,
        paths=paths,
        node_ids=unique_node_ids,
    )


def read_file_lines(
    root: Path,
    rel_file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> tuple[str, int, int]:
    """Safely read a 1-indexed line slice from a file within the project root."""
    resolved_root = root.resolve()
    target_file = (resolved_root / rel_file_path).resolve()
    if not target_file.is_relative_to(resolved_root):
        raise ValueError(f"Path traversal blocked: '{rel_file_path}' is outside project root")
    if not target_file.is_file():
        raise FileNotFoundError(f"Source file not found: '{rel_file_path}'")

    lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if total == 0:
        return "", 0, 0
    s_line = max(1, start_line or 1)
    e_line = min(total, end_line or total)
    if s_line > e_line and total > 0:
        s_line = min(s_line, total)
        e_line = s_line

    numbered = [f"{ln}: {lines[ln - 1]}" for ln in range(s_line, e_line + 1)]
    return "\n".join(numbered), s_line, e_line


def get_code_slice(
    db: IndexerDB,
    root: Path,
    symbol_name: str,
    file_path: str | None = None,
    include_callees: bool = True,
) -> CodeSliceResult:
    """Extract the exact source lines (start_line..end_line) and callee contracts for a symbol."""
    norm_file: str | None = None
    if file_path:
        p = Path(file_path)
        if not p.is_absolute():
            p = root / p
        try:
            norm_file = str(p.resolve().relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            norm_file = file_path.replace("\\", "/")

    matches = db.find_nodes_by_name(symbol_name, file_path=norm_file)
    if not matches and not norm_file:
        matches = db.search_ast_exact_or_prefix(symbol_name, limit=5)

    if not matches:
        return CodeSliceResult(found=False, name=symbol_name, qualified_name=symbol_name)

    target = matches[0]
    nid = target["id"]
    rel_path = target["file_path"]
    s_line = target.get("start_line")
    e_line = target.get("end_line")

    try:
        snippet, actual_start, actual_end = read_file_lines(root, rel_path, s_line, e_line)
    except (FileNotFoundError, ValueError):
        snippet = ""
        actual_start = s_line or 0
        actual_end = e_line or 0

    callees: list[CalleeContract] = []
    if include_callees:
        seen_callees: set[tuple[str, int | None]] = set()
        for row in db.get_neighbors(nid, "down"):
            if row["relation_type"] != "calls":
                continue
            key = (row["neighbor_id"], row.get("call_line"))
            if key in seen_callees:
                continue
            seen_callees.add(key)
            callees.append(
                CalleeContract(
                    id=row["neighbor_id"],
                    name=row["name"],
                    qualified_name=row.get("qualified_name") or row["name"],
                    file_path=row["file_path"],
                    start_line=row.get("start_line"),
                    signature=row.get("signature"),
                    call_line=row.get("call_line"),
                )
            )

    return CodeSliceResult(
        found=True,
        node_id=nid,
        name=target["name"],
        qualified_name=target.get("qualified_name") or target["name"],
        kind=target.get("kind"),
        file_path=rel_path,
        start_line=actual_start,
        end_line=actual_end,
        signature=target.get("signature"),
        docstring=target.get("docstring"),
        source_code=snippet,
        callees=callees,
    )


def notify_ui_focus(
    event_type: str,
    node_ids: list[str],
    summary: str = "",
    from_symbol: str | None = None,
    to_symbol: str | None = None,
) -> None:
    """Fire-and-forget notification to local Map Room server (/api/agent-focus)."""
    import json
    import os
    import threading
    import urllib.request

    base_url = (
        os.getenv("VIBETRACE_SERVER_URL")
        or f"http://127.0.0.1:{os.getenv('VIBETRACE_PORT', '8000')}"
    )
    focus_url = f"{base_url.rstrip('/')}/api/agent-focus"

    payload = json.dumps(
        {
            "type": "agent_focus",
            "event_type": event_type,
            "node_ids": node_ids,
            "summary": summary,
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
        }
    ).encode("utf-8")

    def _send() -> None:
        try:
            req = urllib.request.Request(
                focus_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=0.15):
                pass
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()
