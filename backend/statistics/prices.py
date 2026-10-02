"""Public perpetual prices for statistics; source exchange is preserved."""
import time
from backend.marketdata.adapters import RestAdapter,BybitAdapter
from backend.marketdata.timeframes import binance_interval
from backend.marketdata.models import Bar
class BybitStatisticsAdapter(BybitAdapter):
    async def funding_history(self,symbol,start,end):
        payload=await self._get('/v5/market/funding/history',dict(category='linear',symbol=symbol,startTime=start,endTime=end,limit=200))
        rows=payload['result']['list']
        if len(rows)>=200:raise ValueError('Funding history truncated')
        return [{'time':int(r['fundingRateTimestamp']),'rate':float(r['fundingRate'])} for r in rows]

class BinancePerpetualAdapter(RestAdapter):
    def __init__(self,retries=1):super().__init__(None,'https://fapi.binance.com',retries)
    async def klines(self,symbol,timeframe,limit=1000,end=None,start=None):
        rows=await self._get('/fapi/v1/klines',dict(symbol=symbol,interval=binance_interval(timeframe),
            limit=min(limit,1000),endTime=end,startTime=start))
        now=int((await self._get('/fapi/v1/time',{}))['serverTime'])
        return sorted([Bar('BINANCE',symbol,timeframe,int(r[0]),int(r[6])+1,*map(float,r[1:6]),
            confirmed=int(r[6])<now,received_at=int(time.time()*1000),turnover=float(r[7])) for r in rows],key=lambda b:b.start)
    async def funding_history(self,symbol,start,end):
        rows=await self._get('/fapi/v1/fundingRate',dict(symbol=symbol,startTime=start,endTime=end,limit=1000))
        if len(rows)>=1000:raise ValueError('Funding history truncated')
        return [{'time':int(r['fundingTime']),'rate':float(r['fundingRate']),'mark_price':float(r['markPrice'])} for r in rows]
