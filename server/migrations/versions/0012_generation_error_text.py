"""generations.error as Text: it also carries a follow-up's answer, question or new-topic reply, and at String(1000)
answers were cut mid-word

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.alter_column("error", existing_type=sa.String(length=1000), type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    # Longer messages are cut to fit the old column first (Postgres refuses to shorten a column holding longer values).
    op.execute("UPDATE generations SET error = substr(error, 1, 1000) WHERE length(error) > 1000")
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.alter_column("error", existing_type=sa.Text(), type_=sa.String(length=1000), existing_nullable=True)
