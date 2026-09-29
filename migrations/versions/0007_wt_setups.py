"""Independent WT states and events, sharing market_bars with BROKE (deprecated)."""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    op.drop_table('wt_events', if_exists=True)
    op.drop_table('wt_current', if_exists=True)
