"""Actual PostgreSQL/Redis smoke, isolated schema, mocked Telegram only."""
import asyncio,json,os,uuid
import httpx
from sqlalchemy import create_engine,text,select
from sqlalchemy.engine import make_url
from redis.asyncio import Redis
from backend.models.repository import Repository
from backend.models.schema import Delivery
from backend.api.main import create_app
from backend.alerts.notifier import deliver_one
from fastapi.testclient import TestClient

async def main():
    name='validation_'+uuid.uuid4().hex[:12]
    original=make_url(os.environ['DATABASE_URL']);admin=create_engine(original)
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA {name}'))
    repo=Repository(original.update_query_dict({'options':f'-csearch_path={name}'}).render_as_string(hide_password=False))
    try:
        repo.initialize()
        with TestClient(create_app(repo)) as api:
            response=api.post('/api/alerts/rules',json={'name':'Isolated smoke','conditions':{'field':'avg_setup','op':'>=','value':70},'cooldown_seconds':0})
            assert response.status_code==201,response.text
            sample={'exchange':'BYBIT','symbol':'VALIDATIONUSDT','timeframe':'15','event_time':1700000100000,'bar_start':1699999200000,'confirmed':True,'setup_generation_id':'g1','action':'WATCH','fsm':2,'direction':'LONG','avg_setup':72,'price':100,'signals':['L WATCH'],'data_health':'HEALTHY','parameter_set_id':repo.parameters()['id'],'metrics':{}}
            repo.save_snapshot(sample,{'check':'durable'});repo.save_snapshot(sample)
            with repo.session() as session:assert len(session.scalars(select(Delivery)).all())==1
            assert repo.load_checkpoint('BYBIT','VALIDATIONUSDT','15')=={'check':'durable'}
            repo.save_bars([dict(exchange='BYBIT',symbol='VALIDATIONUSDT',timeframe='15',start=0,end=900000,open=1.,high=2.,low=1.,close=2.,volume=3.,turnover=5.,confirmed=True)])
            assert api.get('/api/setups/VALIDATIONUSDT/15/bars').json()['items'][0]['turnover']==5.
            called=[]
            def handler(request):called.append(request);return httpx.Response(200,json={'ok':True})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
                await deliver_one(repo,transport,'MOCK','MOCK')
            assert len(called)==1
        redis=Redis.from_url(os.environ['REDIS_URL'],decode_responses=True);sub=redis.pubsub();channel='validation:'+name
        await sub.subscribe(channel);await redis.publish(channel,'ok')
        found=False
        for _ in range(5):
            message=await sub.get_message(ignore_subscribe_messages=True,timeout=1)
            if message and message['data']=='ok':found=True;break
        assert found
        await sub.aclose();await redis.aclose()
        print(json.dumps({'postgresql':'PASS','redis_pubsub':'PASS','api':'PASS','durable_dedupe':'PASS','telegram_mock':'PASS','actual_telegram_messages':0}))
    finally:
        repo.engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA {name} CASCADE'))
        admin.dispose()
if __name__=='__main__':asyncio.run(main())
