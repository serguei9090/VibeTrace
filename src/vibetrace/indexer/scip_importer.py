"""SCIP (Sourcegraph Code Intelligence Protocol) index importer for VibeTrace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node


def parse_scip_symbol_name(scip_sym: str) -> tuple[str, str]:
    """Extract clean symbol name and qualified name from a SCIP symbol string."""
    if not scip_sym or scip_sym.startswith("local "):
        cleaned = scip_sym.replace("local ", "")
        return cleaned, cleaned

    parts = scip_sym.split()
    descriptor_part = parts[-1] if parts else scip_sym
    clean_desc = descriptor_part.rstrip("/").replace("/", ".")
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", clean_desc) if t]
    if not tokens:
        return scip_sym, scip_sym
    name = tokens[-1]
    qname = ".".join(tokens)
    return name, qname


class ScipImporter:
    """Imports SCIP JSON or protobuf index data into VibeTrace IndexerDB."""

    @classmethod
    def import_scip_json_dict(
        cls,
        data: dict[str, Any],
        db: IndexerDB,
        project_root: Path | None = None,
    ) -> dict[str, int]:
        """Import from a parsed SCIP JSON dictionary (e.g. from `scip print --json`)."""
        documents = data.get("documents", [])
        nodes_created = 0
        edges_created = 0
        files_count = len(documents)

        # Temporary maps: symbol_name -> Node, file -> list of occurrences
        defs_by_symbol: dict[str, Node] = {}
        refs: list[tuple[str, str, int]] = []  # (caller_file, target_symbol, call_line)

        for doc in documents:
            rel_path = doc.get("relative_path", "")
            if not rel_path:
                continue

            occurrences = doc.get("occurrences", [])
            symbols_meta = {
                s.get("symbol", ""): s.get("documentation", []) for s in doc.get("symbols", [])
            }

            for occ in occurrences:
                sym_str = occ.get("symbol", "")
                if not sym_str:
                    continue

                roles = occ.get("symbol_roles", 0)
                rng = occ.get("range", [1, 0, 1, 0]) or [1, 0, 1, 0]
                start_line = (rng[0] + 1) if len(rng) > 0 else 1
                end_line = (rng[2] + 1) if len(rng) >= 3 else start_line

                is_definition = bool(roles & 1)  # Role: Definition = 1

                if is_definition:
                    name, qname = parse_scip_symbol_name(sym_str)
                    docs = symbols_meta.get(sym_str, [])
                    docstring = "\n".join(docs) if isinstance(docs, list) else str(docs)

                    # Determine symbol kind
                    kind = "function"
                    if "#" in sym_str and not sym_str.endswith("#"):
                        kind = "method"
                    elif sym_str.endswith("#"):
                        kind = "class"

                    node_id = f"{rel_path}:{start_line}:{name}"
                    node = Node(
                        id=node_id,
                        name=name,
                        qualified_name=qname,
                        kind=kind,
                        file_path=rel_path,
                        start_line=start_line,
                        end_line=end_line,
                        signature=f"{name}()",
                        docstring=docstring or None,
                    )
                    defs_by_symbol[sym_str] = node
                else:
                    # Role: Read / Call / Reference
                    refs.append((rel_path, sym_str, start_line))

        # 1. Upsert all definition nodes
        for node in defs_by_symbol.values():
            db.upsert_node(node)
            nodes_created += 1

        # 2. Resolve caller nodes and insert edges
        for caller_file, target_sym, call_line in refs:
            target_node = defs_by_symbol.get(target_sym)
            if not target_node:
                continue

            # Find enclosing node for the caller file at this call line
            caller_nodes = db.get_nodes_in_file(caller_file)
            enclosing_id = None
            for c in caller_nodes:
                s_line = c.get("start_line") or 0
                e_line = c.get("end_line") or 999999
                if s_line <= call_line <= e_line:
                    enclosing_id = c.get("id")
                    break

            from_id = enclosing_id or f"{caller_file}::caller"
            edge = Edge(
                from_node_id=from_id,
                to_node_id=target_node.id,
                relation_type="calls",
                call_line=call_line,
            )
            db.upsert_edge(edge)
            edges_created += 1

        return {
            "files_processed": files_count,
            "nodes_imported": nodes_created,
            "edges_imported": edges_created,
        }

    @classmethod
    def import_scip_file(
        cls,
        file_path: Path | str,
        db: IndexerDB,
        project_root: Path | None = None,
    ) -> dict[str, int]:
        """Reads a SCIP JSON file and imports all nodes and edges into IndexerDB."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"SCIP index file not found: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.import_scip_json_dict(data, db, project_root)
