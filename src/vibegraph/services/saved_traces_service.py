"""Service for managing saved architectural trace paths."""

from __future__ import annotations

from pydantic import BaseModel, Field

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.db_retry import with_write_retry


class SavedTrace(BaseModel):
    """Model representing a saved architectural tracepath query."""

    id: int
    name: str
    description: str = ""
    from_symbol: str
    to_symbol: str
    max_depth: int = 6
    created_at: str | None = None


class SavedTraceCreate(BaseModel):
    """Payload to create or update a saved architectural tracepath."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    from_symbol: str = Field(..., min_length=1)
    to_symbol: str = Field(..., min_length=1)
    max_depth: int = Field(default=6, ge=1, le=20)


def list_saved_traces(db: IndexerDB) -> list[SavedTrace]:
    """Return all saved trace queries ordered by name."""
    with db._get_conn() as conn:
        cursor = conn.execute(
            "SELECT id, name, description, from_symbol, to_symbol, max_depth, created_at "
            "FROM saved_traces ORDER BY name ASC"
        )
        return [
            SavedTrace(
                id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                from_symbol=row["from_symbol"],
                to_symbol=row["to_symbol"],
                max_depth=row["max_depth"],
                created_at=str(row["created_at"]) if row["created_at"] else None,
            )
            for row in cursor.fetchall()
        ]


def get_saved_trace_by_name(db: IndexerDB, name: str) -> SavedTrace | None:
    """Retrieve a saved trace query by name."""
    with db._get_conn() as conn:
        cursor = conn.execute(
            "SELECT id, name, description, from_symbol, to_symbol, max_depth, created_at "
            "FROM saved_traces WHERE name = ?",
            (name,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return SavedTrace(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            from_symbol=row["from_symbol"],
            to_symbol=row["to_symbol"],
            max_depth=row["max_depth"],
            created_at=str(row["created_at"]) if row["created_at"] else None,
        )


@with_write_retry()
def create_saved_trace(
    db: IndexerDB,
    payload: SavedTraceCreate,
) -> SavedTrace:
    """Create or replace a saved architectural trace path."""
    with db._get_conn() as conn:
        conn.execute(
            "INSERT INTO saved_traces (name, description, from_symbol, to_symbol, max_depth) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "description = excluded.description, "
            "from_symbol = excluded.from_symbol, "
            "to_symbol = excluded.to_symbol, "
            "max_depth = excluded.max_depth",
            (
                payload.name,
                payload.description,
                payload.from_symbol,
                payload.to_symbol,
                payload.max_depth,
            ),
        )
        conn.commit()

    saved = get_saved_trace_by_name(db, payload.name)
    if not saved:
        raise RuntimeError(f"Failed to persist saved trace '{payload.name}'")
    return saved


@with_write_retry()
def delete_saved_trace(db: IndexerDB, trace_id: int) -> bool:
    """Delete a saved trace by ID. Returns True if deleted, False if not found."""
    with db._get_conn() as conn:
        cursor = conn.execute("DELETE FROM saved_traces WHERE id = ?", (trace_id,))
        conn.commit()
        return cursor.rowcount > 0
