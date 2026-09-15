"""Lossless monthly partition conversion of analytical history (PostgreSQL)."""
from datetime import datetime,timezone
from alembic import op
import sqlalchemy as sa
from backend.partitions import PARENTS,child_name,month_start,shift_month

revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None


def convert(connection,parent,partitioned):
    quote=connection.dialect.identifier_preparer.quote
    connection.execute(sa.text('LOCK TABLE '+parent+' IN ACCESS EXCLUSIVE MODE'))
    customized=connection.scalar(sa.text('''SELECT relrowsecurity OR relacl IS NOT NULL
        OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal)
        FROM pg_class c WHERE c.oid=to_regclass(:parent)'''),{'parent':parent})
    if customized:raise RuntimeError('Review custom table grants, RLS or triggers before conversion')
    # Preserve all ordinary indexes, including ones added by an operator.
    indexes=connection.execute(sa.text('''SELECT pg_get_indexdef(indexrelid) AS definition
        FROM pg_index WHERE indrelid=to_regclass(:parent) AND NOT indisprimary'''),{'parent':parent}).scalars().all()
    if partitioned:
        unique=connection.scalar(sa.text('SELECT count(*) FROM pg_index WHERE indrelid=to_regclass(:parent) AND indisunique AND NOT indisprimary'),{'parent':parent})
        if unique:raise RuntimeError('Review custom unique indexes before partition conversion')
    sequence=connection.scalar(sa.text("SELECT pg_get_serial_sequence(:parent,'id')"),{'parent':parent})
    replacement=parent+'_replacement';legacy=parent+'_legacy'
    suffix=' PARTITION BY RANGE (event_time)' if partitioned else ''
    connection.execute(sa.text('CREATE TABLE '+replacement+' (LIKE '+parent+' INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING STORAGE INCLUDING COMMENTS)'+suffix))
    connection.execute(sa.text('ALTER TABLE '+replacement+' ADD PRIMARY KEY (id'+(',event_time' if partitioned else '')+')'))
    foreign_keys=sa.inspect(connection).get_foreign_keys(parent)
    for key in foreign_keys:
        target=(quote(key['referred_schema'])+'.' if key['referred_schema'] else '')+quote(key['referred_table'])
        connection.execute(sa.text('ALTER TABLE '+replacement+' ADD FOREIGN KEY ('+','.join(map(quote,key['constrained_columns']))+') REFERENCES '+target+' ('+','.join(map(quote,key['referred_columns']))+')'))
    if partitioned:
        actual=connection.scalars(sa.text("SELECT DISTINCT (extract(epoch FROM date_trunc('month',to_timestamp(event_time/1000.0) AT TIME ZONE 'UTC'))*1000)::bigint FROM "+parent)).all()
        current=month_start(int(datetime.now(timezone.utc).timestamp()*1000))
        for start in sorted({*actual,*(shift_month(current,n) for n in range(-1,4))}):
            connection.execute(sa.text('CREATE TABLE '+child_name(parent,start)+' PARTITION OF '+replacement+' FOR VALUES FROM ('+str(start)+') TO ('+str(shift_month(start,1))+')'))
        connection.execute(sa.text('CREATE TABLE '+parent+'_default PARTITION OF '+replacement+' DEFAULT'))
    connection.execute(sa.text('INSERT INTO '+replacement+' SELECT * FROM '+parent))
    mismatch=connection.scalar(sa.text('SELECT EXISTS ((SELECT * FROM '+parent+' EXCEPT ALL SELECT * FROM '+replacement+') UNION ALL (SELECT * FROM '+replacement+' EXCEPT ALL SELECT * FROM '+parent+'))'))
    if mismatch:raise RuntimeError('Partition conversion changed rows; rolling back')
    connection.execute(sa.text('ALTER TABLE '+parent+' RENAME TO '+legacy))
    connection.execute(sa.text('ALTER TABLE '+replacement+' RENAME TO '+parent))
    if sequence:
        # pg_get_serial_sequence returns an already quoted, schema-qualified
        # server identifier. Reassign ownership before dropping the old table.
        connection.execute(sa.text('ALTER SEQUENCE '+sequence+' OWNED BY '+parent+'.id'))
    connection.execute(sa.text('DROP TABLE '+legacy))
    for definition in indexes:connection.execute(sa.text(definition))


def upgrade():
    connection=op.get_bind()
    if connection.dialect.name!='postgresql':return
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
    for parent in PARENTS:convert(connection,parent,True)


def downgrade():
    connection=op.get_bind()
    if connection.dialect.name!='postgresql':return
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
    for parent in PARENTS:convert(connection,parent,False)
