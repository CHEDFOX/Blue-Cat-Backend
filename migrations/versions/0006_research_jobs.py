"""research assistant jobs

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("principal", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'refused')",
            name=op.f("ck_research_jobs_research_job_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_jobs")),
    )
    op.create_index("ix_research_jobs_expires_at", "research_jobs", ["expires_at"], unique=False)
    op.create_index("ix_research_jobs_principal_created", "research_jobs", ["principal", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_research_jobs_principal_created", table_name="research_jobs")
    op.drop_index("ix_research_jobs_expires_at", table_name="research_jobs")
    op.drop_table("research_jobs")
