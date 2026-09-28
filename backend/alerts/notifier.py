"""Transactional Telegram outbox. An ambiguous network send is never retried automatically."""
from __future__ import annotations
import asyncio
import logging
import os
import re
from urllib.parse import quote
import httpx
from sqlalchemy import select
from backend.models.repository import Repository,now_ms
from backend.models.schema import Delivery,Rule,Current,WTCurrent,WTEvent

log=logging.getLogger(__name__)
DEFAULT='''{direction} {event}
Symbol: {symbol} | TF: {timeframe}
Price: {price}
ACTION: {action}
AVG SETUP: {avg_setup}
Formation: {formation} | Execution: {execution}
Geometry: {geometry} | Context: {context}
Level: {level} | Approach: {approach}
MAE: {mae} | Exhaustion: {exhaustion}
BTC Shock: {btc_shock}
Candidate: {candidate_path} | Trigger: {trigger_path}
SL: {sl} | T1: {t1} | R:R: {rr}
Blockers: {blockers}
{detail_url}'''
def render_message(payload):
    if 'text' in payload: return str(payload['text'])[:4096]
    snapshot=payload['snapshot']; values=dict(snapshot)
    values['event']=', '.join(snapshot.get('signals',[])) or payload.get('rule_name','Setup')
    values['blockers']=', '.join(snapshot.get('blockers',[])) or 'NONE'
    base=os.getenv('PUBLIC_BASE_URL','http://localhost:8080').rstrip('/')
    values['detail_url']=base+'/setups/'+'/'.join(quote(str(snapshot.get(k,'')),safe='') for k in ('exchange','symbol','timeframe'))
    if snapshot.get('strategy')=='WT_SETUPS':values['detail_url']=base+'/wt-setups?symbol='+quote(snapshot.get('symbol',''),safe='')
    for key in ('avg_setup','formation','execution','geometry','context','level','approach','mae','exhaustion','btc_shock','continuation','rr','distance'):
        value=values.get(key)
        if isinstance(value,(int,float)) and not isinstance(value,bool):values[key]=f'{value:.2f}'.rstrip('0').rstrip('.')
    template=payload.get('template') or DEFAULT
    # Only literal field names; no attribute access, expressions, or formatting execution.
    return re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}',lambda m:str(values[m[1]]) if values.get(m[1]) is not None else 'n/a',template)[:4096]

async def deliver_one(repo:Repository,client:httpx.AsyncClient,token:str,chat_id:str,now:int|None=None):
    now=now or now_ms()
    with repo.session.begin() as s:
        # Crash after claim can mean Telegram accepted the request. Preserve uncertainty.
        abandoned=s.scalars(select(Delivery).where(Delivery.status=='sending',Delivery.updated_at<now-120000).with_for_update(skip_locked=True)).all()
        for item in abandoned: item.status='uncertain'; item.error='Worker interrupted during delivery; inspect Telegram before manual retry'
        row=s.scalar(select(Delivery).where(Delivery.status.in_(['pending','retry']),Delivery.next_attempt<=now).order_by(Delivery.id).with_for_update(skip_locked=True).limit(1))
        if row is None: return False
        if row.rule_id=='wt-tradingview':
            reference=s.get(WTEvent,row.payload.get('wt_reference_id'))
            if reference is None or os.getenv('WT_TRADINGVIEW_TELEGRAM_ENABLED','true').lower()!='true' or row.created_at<now-int(os.getenv('ALERT_MAX_AGE_SEC','120'))*1000:
                row.status='suppressed';row.error='WT reference expired or forwarding disabled';row.updated_at=now;return True
        elif row.rule_id!='manual-test':
            rule=s.get(Rule,row.rule_id)
            snapshot=row.payload['snapshot']; market=tuple(snapshot[k] for k in ('exchange','symbol','timeframe'))
            current=s.get(WTCurrent,(*market,snapshot.get('signal_source','engine'))) if snapshot.get('strategy')=='WT_SETUPS' else s.get(Current,market)
            health=current.payload.get('data_health') if current else None
            if isinstance(health,dict): health=health.get('status')
            if rule is None or not rule.enabled or rule.version!=row.rule_version or health not in {'HEALTHY','FULL_REALTIME','KLINE_REALTIME'} or current.updated_at<now-90000:
                row.status='suppressed'; row.error='Rule changed/disabled or market data no longer healthy'; row.updated_at=now; return True
            if row.created_at<now-int(os.getenv('ALERT_MAX_AGE_SEC','120'))*1000:
                row.status='suppressed'; row.error='Alert expired before delivery'; row.updated_at=now; return True
        row.status='sending'; row.updated_at=now; row.attempts+=1
        row_id=row.id; payload=row.payload; attempt=row.attempts
    status='failed'; error=None; retry_at=0
    try:
        response=await client.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat_id,'text':render_message(payload),'disable_web_page_preview':True},timeout=15)
        data=response.json()
        if response.status_code==200 and data.get('ok'): status='sent'
        elif response.status_code==429:
            status='retry'; retry_at=now+max(1,int(data.get('parameters',{}).get('retry_after',30)))*1000; error='Telegram rate limit'
        else:
            # Telegram explicitly rejected it: safe to retry transient responses.
            status='retry' if response.status_code>=500 and attempt<5 else 'failed'
            retry_at=now+min(300,2**attempt)*1000
            error=f'Telegram rejected request (HTTP {response.status_code})'
    except httpx.ConnectError:
        status='retry' if attempt<5 else 'failed'; retry_at=now+min(300,2**attempt)*1000; error='Connection could not be established'
    except (httpx.HTTPError,ValueError):
        status='uncertain'; error='Delivery response unavailable; inspect Telegram before retry'
    with repo.session.begin() as s:
        row=s.get(Delivery,row_id); row.status=status; row.error=error; row.updated_at=now_ms(); row.next_attempt=retry_at
    return True

async def main():
    repo=Repository(); repo.initialize()
    token=os.getenv('TELEGRAM_BOT_TOKEN',''); chat=os.getenv('TELEGRAM_CHAT_ID','')
    async with httpx.AsyncClient() as client:
        while True:
            enabled=bool(token and chat)
            repo.heartbeat('notifier',{'status':'HEALTHY','telegram':'configured' if enabled else 'disabled'})
            if enabled:
                if await deliver_one(repo,client,token,chat): continue
            await asyncio.sleep(2)

if __name__=='__main__': asyncio.run(main())
