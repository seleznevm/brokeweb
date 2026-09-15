"""Lossless compressed checkpoints; keep the legacy JSON reader for upgrades."""
from alembic import op
import sqlalchemy as sa
from backend.models.checkpoints import unpack_checkpoint
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None


def upgrade():
    if 'checkpoint_blob' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('setup_current')}:
        op.add_column('setup_current',sa.Column('checkpoint_blob',sa.LargeBinary(),nullable=True))


def downgrade():
    # Restore JSON before removing the new column; never discard saved state.
    table=sa.table('setup_current',sa.column('exchange',sa.String()),sa.column('symbol',sa.String()),
                   sa.column('timeframe',sa.String()),sa.column('checkpoint',sa.JSON()),
                   sa.column('checkpoint_blob',sa.LargeBinary()))
    connection=op.get_bind()
    for row in connection.execute(sa.select(table).where(table.c.checkpoint_blob.is_not(None))).mappings():
        connection.execute(table.update().where(table.c.exchange==row['exchange'],table.c.symbol==row['symbol'],
                           table.c.timeframe==row['timeframe']).values(checkpoint=unpack_checkpoint(row['checkpoint_blob'])))
    op.drop_column('setup_current','checkpoint_blob')
