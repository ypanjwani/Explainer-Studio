"""users.google_sub and users.email_verified_at; password_hash optional: "Continue with Google" accounts

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_sub', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('email_verified_at', sa.BigInteger(), nullable=True))
        batch_op.alter_column('password_hash', existing_type=sa.String(length=100), nullable=True)
        batch_op.create_index(batch_op.f('ix_users_google_sub'), ['google_sub'], unique=True)


def downgrade() -> None:
    # Google-only accounts have no password; they can't exist without this column change.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_google_sub'))
        batch_op.alter_column('password_hash', existing_type=sa.String(length=100), nullable=False)
        batch_op.drop_column('email_verified_at')
        batch_op.drop_column('google_sub')
