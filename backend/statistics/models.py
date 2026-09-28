from sqlalchemy import BigInteger,Integer,String,Index
from sqlalchemy.orm import Mapped,mapped_column
from backend.models.schema import Base,J
class Evaluation(Base):
    __tablename__='signal_evaluations'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    policy:Mapped[str]=mapped_column(String(80))
    strategy:Mapped[str]=mapped_column(String(30))
    source:Mapped[str]=mapped_column(String(20))
    mode:Mapped[str]=mapped_column(String(20))
    family:Mapped[str]=mapped_column(String(40))
    exchange:Mapped[str]=mapped_column(String(30))
    symbol:Mapped[str]=mapped_column(String(60))
    timeframe:Mapped[str]=mapped_column(String(16))
    direction:Mapped[str]=mapped_column(String(16))
    event_time:Mapped[int]=mapped_column(BigInteger)
    updated_at:Mapped[int]=mapped_column(BigInteger,default=0)
    status:Mapped[str]=mapped_column(String(30),default='OPEN')
    plan:Mapped[dict]=mapped_column(J)
    outcome:Mapped[dict]=mapped_column(J,default=dict)
    __table_args__=(Index('ix_evaluation_cohort','strategy','source','mode','event_time'),Index('ix_evaluation_pending','status','updated_at'))
class CaptureCursor(Base):
    __tablename__='statistics_cursors'
    name:Mapped[str]=mapped_column(String(30),primary_key=True)
    last_id:Mapped[int]=mapped_column(Integer,default=0)
