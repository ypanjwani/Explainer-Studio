"""shares: public "Share link" copies of an explainer's spec (/s/<id>)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('shares',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.Column('updated_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('shares', schema=None) as batch_op:
        batch_op.create_index('ix_shares_user_record', ['user_id', 'record_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('shares', schema=None) as batch_op:
        batch_op.drop_index('ix_shares_user_record')
    op.drop_table('shares')
