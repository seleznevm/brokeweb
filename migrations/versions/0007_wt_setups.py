"""Independent WT states and events, sharing market_bars with BROKE."""
from alembic import op
from backend.models.schema import WTCurrent,WTEvent
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None
def upgrade():
    for model in (WTCurrent,WTEvent):model.__table__.create(op.get_bind(),checkfirst=True)
def downgrade():
    op.drop_table('wt_events');op.drop_table('wt_current')
