"""page_versions + page_lines: the version store (versions.py, follow-ups v5 phase 1); generations.meta; and a one-off
cleanup clearing generations.spec where the record already holds the finished page

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The finished page is in the record (status done), so the generation's copy is never read again.
CLEAR_APPLIED = (
    "UPDATE generations SET spec = NULL WHERE status = 'done' AND spec IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM explainers e WHERE e.user_id = generations.user_id AND e.id = generations.record_id AND e.status = 'done')"
)


def upgrade() -> None:
    op.create_table('page_lines',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('line', sa.String(length=64), nullable=False),
    sa.Column('thread', sa.String(length=64), nullable=True),
    sa.Column('next_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'line')
    )
    op.create_table('page_versions',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('line', sa.String(length=64), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.String(length=64), nullable=False),
    sa.Column('parent', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('payload', sa.LargeBinary(), nullable=True),
    sa.Column('main_file', sa.LargeBinary(), nullable=True),
    sa.Column('section_ids', sa.JSON(), nullable=False),
    sa.Column('log', sa.JSON(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.Column('updated_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'line', 'version')
    )
    with op.batch_alter_table('page_versions', schema=None) as batch_op:
        batch_op.create_index('ix_page_versions_user_record', ['user_id', 'record_id'], unique=False)
    with op.batch_alter_table('generations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('meta', sa.JSON(), nullable=True))
    op.execute(CLEAR_APPLIED)


def downgrade() -> None:
    with op.batch_alter_table('generations', schema=None) as batch_op:
        batch_op.drop_column('meta')
    with op.batch_alter_table('page_versions', schema=None) as batch_op:
        batch_op.drop_index('ix_page_versions_user_record')
    op.drop_table('page_versions')
    op.drop_table('page_lines')
