import json,logging,os,datetime
class JsonFormatter(logging.Formatter):
    def format(self,record):
        result={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'level':record.levelname,'logger':record.name,'message':record.getMessage()}
        for key in ('symbol','timeframe','generation','bar_timestamp','action','fsm','event_type','error','processed','total','latency_ms'):
            if hasattr(record,key):result[key]=getattr(record,key)
        if record.exc_info:result['exception']=self.formatException(record.exc_info)
        return json.dumps(result,ensure_ascii=False)
def configure_logging():
    handler=logging.StreamHandler();handler.setFormatter(JsonFormatter());logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),handlers=[handler],force=True)
    logging.getLogger('httpx').setLevel(logging.WARNING)
