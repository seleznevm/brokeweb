"""Liveness check: recovery may take time; an absent heartbeat is a failure."""
import sys,os
from backend.models.repository import Repository,now_ms
from backend.models.schema import ServiceHealth
repo=Repository()
with repo.session() as session:
    name=sys.argv[1]
    if name=='engine' and int(os.getenv('ENGINE_SHARD_COUNT','1'))>1:name+=':'+os.getenv('ENGINE_SHARD_INDEX','0')
    row=session.get(ServiceHealth,name)
    sys.exit(0 if row and now_ms()-row.updated_at<90000 else 1)
