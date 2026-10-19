"""indexes for finding a law by its name, abbreviation or number (citation lookup)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Identical to ulis.retrieval.citations.INSTRUMENT_NAMES and VERSION_NAMES (without the table aliases), or
# PostgreSQL will not use the indexes.
INSTRUMENT_NAMES = (
    "to_tsvector('simple', coalesce(short_title, '') || ' ' || coalesce(number, '') || ' ' || "
    "coalesce(ext ->> 'jurabk', '') || ' ' || coalesce(ext ->> 'amtabk', '') || ' ' || "
    "coalesce(ext ->> 'abbrev', ''))"
)
VERSION_NAMES = "to_tsvector('simple', coalesce(short_title, '') || ' ' || left(title, 300))"


def upgrade() -> None:
    op.execute(f"CREATE INDEX ix_instruments_names ON instruments USING gin (({INSTRUMENT_NAMES}))")
    op.execute(f"CREATE INDEX ix_instrument_versions_names ON instrument_versions USING gin (({VERSION_NAMES}))")


def downgrade() -> None:
    op.execute("DROP INDEX ix_instrument_versions_names")
    op.execute("DROP INDEX ix_instruments_names")
