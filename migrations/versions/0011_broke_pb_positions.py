"""Add broke_pb_positions table."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

J = sa.JSON().with_variant(JSONB, 'postgresql')

def upgrade():
    op.create_table(
        'broke_pb_positions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(60), nullable=False),
        sa.Column('exchange', sa.String(30), nullable=False, server_default='BYBIT'),
        sa.Column('timeframe', sa.String(16), nullable=False, server_default='30'),
        sa.Column('direction', sa.String(16), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='OPEN'),
        sa.Column('nominal_usdt', sa.Float(), nullable=False, server_default='500.0'),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('entry_time', sa.BigInteger(), nullable=False),
        sa.Column('bar_start', sa.BigInteger(), nullable=False),
        sa.Column('sl', sa.Float(), nullable=False),
        sa.Column('tp1', sa.Float(), nullable=False),
        sa.Column('runner', sa.Float(), nullable=True),
        sa.Column('tp1_hit', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('runner_hit', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('runner_be', sa.Float(), nullable=True),
        sa.Column('underwater', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('reduced', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('exhaustion_taken', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('close_price', sa.Float(), nullable=True),
        sa.Column('close_time', sa.BigInteger(), nullable=True),
        sa.Column('close_reason', sa.String(60), nullable=True),
        sa.Column('pnl_usdt', sa.Float(), nullable=True),
        sa.Column('pnl_pct', sa.Float(), nullable=True),
        sa.Column('payload', J, nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_pb_pos_symbol_status', 'broke_pb_positions', ['symbol', 'status'])
    op.create_index('ix_pb_pos_status_entry', 'broke_pb_positions', ['status', 'entry_time'])


def downgrade():
    op.drop_table('broke_pb_positions')
