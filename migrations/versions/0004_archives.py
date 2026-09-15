"""Archive catalogue and chronological indexes for bounded retention batches."""
from alembic import op
import sqlalchemy as sa
from backend.models.schema import ArchiveBatch

revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None
INDEXES=(('ix_snapshot_retention','setup_snapshots'),('ix_event_retention','setup_events'))


def upgrade():
    ArchiveBatch.__table__.create(op.get_bind(),checkfirst=True)
    for name,table in INDEXES:
        if name not in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}:
            if op.get_bind().dialect.name=='postgresql':
                with op.get_context().autocommit_block():
                    op.create_index(name,table,['event_time','id'],postgresql_concurrently=True)
            else:op.create_index(name,table,['event_time','id'])


def downgrade():
    # A catalogue entry may be the only pointer to history outside PostgreSQL.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM archive_batches')):
        raise RuntimeError('Restore/export and preserve the archive catalogue before downgrading 0004')
    for name,table in INDEXES:op.drop_index(name,table_name=table)
    op.drop_table('archive_batches')
