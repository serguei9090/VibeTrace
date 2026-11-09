"""Repo Map generator with PageRank centrality pruning and token budget constraints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.db_queries import _clean_path

CHARS_PER_TOKEN = 3.5  # Conservative empirical ratio with safety headroom for code tokens


def estimate_tokens(text: str) -> int:
    """Fast empirical token estimation for code and markdown."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


class RepoMapService:
    """Generates compact, centrality-ranked repository maps bounded by a token budget."""

    @classmethod
    def generate_budgeted_repo_map(
        cls,
        db: IndexerDB,
        scope_path: str = ".",
        budget_tokens: int = 1024,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Build a PageRank-prioritized symbol map strictly bounded within budget_tokens."""
        clean_scope = _clean_path(scope_path).rstrip("/")
        is_root = clean_scope in ("", ".", "./")

        with db._get_conn() as conn:
            if is_root:
                cursor = conn.execute(
                    """
                    SELECT id, name, qualified_name, parent_id, kind, signature,
                           start_line, end_line, decorators, visibility, file_path, centrality
                    FROM nodes
                    WHERE kind IN ('function', 'class', 'method', 'endpoint', 'struct', 'trait')
                    ORDER BY centrality DESC, file_path ASC, start_line ASC
                    """
                )
            else:
                like_pattern = f"{clean_scope}/%"
                cursor = conn.execute(
                    """
                    SELECT id, name, qualified_name, parent_id, kind, signature,
                           start_line, end_line, decorators, visibility, file_path, centrality
                    FROM nodes
                    WHERE (file_path = ? OR file_path LIKE ?)
                      AND kind IN ('function', 'class', 'method', 'endpoint', 'struct', 'trait')
                    ORDER BY centrality DESC, file_path ASC, start_line ASC
                    """,
                    (clean_scope, like_pattern),
                )
            all_rows = [dict(r) for r in cursor.fetchall()]

        total_symbols = len(all_rows)
        if total_symbols == 0:
            return {
                "scope_path": scope_path,
                "budget_tokens": budget_tokens,
                "estimated_tokens": 0,
                "total_symbols": 0,
                "included_symbols": 0,
                "pruned_symbols": 0,
                "files": {},
                "formatted_map": f"No symbols found in scope `{scope_path}`.",
            }

        # Greedily include symbols by descending centrality until token budget is consumed
        selected_by_file: dict[str, list[dict[str, Any]]] = {}
        included_count = 0
        current_text = (
            f"# Repository Map (Scope: `{scope_path}` | Budget: {budget_tokens} tokens)\n\n"
        )
        current_chars = len(current_text)

        # Reserve headroom for footer disclaimer if pruning occurs
        footer_reserve = 20
        effective_budget = max(budget_tokens - footer_reserve, 32)
        max_chars = int(effective_budget * CHARS_PER_TOKEN)

        for row in all_rows:
            fpath = row["file_path"]
            name = row["name"]
            kind = row["kind"]
            sig = row.get("signature") or ""
            if sig.startswith("("):
                sig = f"{name}{sig}"
            elif not sig:
                sig = f"{name}()"
            sig = " ".join(sig.split())
            sline = row.get("start_line") or 1

            # Format line candidate
            prefix = "  " if kind in ("method", "function") else ""
            line_str = f"{prefix}{kind} {sig} (L{sline})\n"

            # Check if this file is already in the map
            header_str = f"{fpath}:\n" if fpath not in selected_by_file else ""
            added_chars = len(header_str) + len(line_str)

            if current_chars + added_chars > max_chars:
                # Stop if adding this symbol exceeds the effective budget envelope
                break

            if fpath not in selected_by_file:
                selected_by_file[fpath] = []
                current_text += header_str

            selected_by_file[fpath].append(row)
            current_text += line_str
            current_chars += added_chars
            included_count += 1

        pruned_count = total_symbols - included_count
        if pruned_count > 0:
            current_text += (
                f"\n[+ {pruned_count} lower-centrality symbols pruned "
                f"to fit {budget_tokens} token budget]"
            )

        return {
            "scope_path": scope_path,
            "budget_tokens": budget_tokens,
            "estimated_tokens": estimate_tokens(current_text),
            "total_symbols": total_symbols,
            "included_symbols": included_count,
            "pruned_symbols": pruned_count,
            "files": {
                f: [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "signature": r.get("signature"),
                        "start_line": r.get("start_line"),
                        "centrality": r.get("centrality", 0.0),
                    }
                    for r in rows
                ]
                for f, rows in selected_by_file.items()
            },
            "formatted_map": current_text.strip(),
        }
