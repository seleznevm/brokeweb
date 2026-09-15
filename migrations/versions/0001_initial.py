"""Initial analytic schema, JSONB on PostgreSQL and JSON for isolated SQLite tests."""
from alembic import op
from backend.models.schema import Base
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade(): Base.metadata.create_all(op.get_bind())
def downgrade(): Base.metadata.drop_all(op.get_bind())
