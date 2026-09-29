"""WT plans and research lifecycle snapshots (deprecated)."""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    op.drop_table('wt_trades', if_exists=True)
