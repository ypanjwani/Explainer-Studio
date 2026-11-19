"""section_notes: each section's part of a follow-ups v5 main file (modes/followup/main_file.py), per user, keyed by the
section's content

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('section_notes',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('note', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.Column('used_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'hash')
    )


def downgrade() -> None:
    op.drop_table('section_notes')
