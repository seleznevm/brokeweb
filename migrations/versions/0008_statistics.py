"""Independent first-hit statistics."""
from alembic import op
from backend.statistics.models import Evaluation,CaptureCursor
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None
def upgrade():
    Evaluation.__table__.create(op.get_bind(),checkfirst=True)
    CaptureCursor.__table__.create(op.get_bind(),checkfirst=True)
def downgrade():
    op.drop_table('statistics_cursors')
    op.drop_table('signal_evaluations')
