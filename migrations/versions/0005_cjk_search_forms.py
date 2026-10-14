"""search forms for scripts written without spaces (CJK bigrams)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TSV = (
    "setweight(to_tsvector(search_config, coalesce(search_context, context, '')), 'B') || "
    "setweight(to_tsvector(search_config, coalesce(search_text, text)), 'A')"
)
OLD_TSV = (
    "setweight(to_tsvector(search_config, coalesce(context, '')), 'B') || "
    "setweight(to_tsvector(search_config, text), 'A')"
)


def _replace_tsv(expression: str) -> None:
    op.drop_index("ix_passages_tsv", table_name="passages", postgresql_using="gin")
    op.drop_column("passages", "tsv")
    op.add_column(
        "passages",
        sa.Column("tsv", sa.dialects.postgresql.TSVECTOR(), sa.Computed(expression, persisted=True), nullable=True),
    )
    op.create_index("ix_passages_tsv", "passages", ["tsv"], unique=False, postgresql_using="gin")


def upgrade() -> None:
    op.add_column("passages", sa.Column("search_context", sa.Text(), nullable=True))
    op.add_column("passages", sa.Column("search_text", sa.Text(), nullable=True))
    _replace_tsv(NEW_TSV)


def downgrade() -> None:
    _replace_tsv(OLD_TSV)
    op.drop_column("passages", "search_text")
    op.drop_column("passages", "search_context")
