"""Drop WT strategy tables and alert rules."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM alert_rules WHERE payload->>'strategy' = 'WT_SETUPS'"))
    op.drop_table('wt_trades', if_exists=True)
    op.drop_table('wt_events', if_exists=True)
    op.drop_table('wt_current', if_exists=True)


def downgrade():
    pass
