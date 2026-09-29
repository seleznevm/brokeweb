"""WT plans and research lifecycle snapshots, sharing the existing candle store."""
from alembic import op
from backend.models.schema import WTTrade

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    WTTrade.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('wt_trades')
