"""Durable TradingView webhook reference inbox."""
from alembic import op
from backend.models.schema import TradingViewAlert

revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None

def upgrade():
    TradingViewAlert.__table__.create(op.get_bind(),checkfirst=True)

def downgrade():
    op.drop_table('tradingview_alerts')
