"""budget circuit breaker flag

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE project ADD COLUMN over_budget boolean NOT NULL DEFAULT false"
    )


def downgrade():
    op.execute("ALTER TABLE project DROP COLUMN over_budget")
