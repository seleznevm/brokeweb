"""Preserve exchange-native quote turnover separately from Pine HLC3 proxy."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    if 'turnover' not in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('market_bars')}:
        op.add_column('market_bars',sa.Column('turnover',sa.Float(),nullable=True))

def downgrade():op.drop_column('market_bars','turnover')
