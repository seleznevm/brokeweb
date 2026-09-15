"""Read-only measurements and optional local engine restart verification."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,timeout=120)


def health():
    with urlopen('http://localhost:8080/api/health',timeout=10) as response:
        return json.load(response)


def wait_healthy(after=0):
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        result=health();engine=result['services'].get('engine',{})
        if result['status']=='HEALTHY' and engine.get('updated_at',0)>after and engine.get('initialized')==engine.get('selected'):
            return result
        time.sleep(5)
    raise RuntimeError('Local engine did not become healthy within 240 seconds')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--restart',action='store_true');args=parser.parse_args()
    output=Path('artifacts/local');output.mkdir(parents=True,exist_ok=True)
    before=wait_healthy();(output/'health-before-restart.json').write_text(json.dumps(before,indent=2))
    if args.restart:
        started=time.time();since=datetime.now(timezone.utc).isoformat()
        docker('compose','restart','engine')
        restarted=time.time();after=wait_healthy(int(started*1000))
        logs=docker('compose','logs','--since',since,'engine')
        result={'status':'PASS','restart_command_seconds':restarted-started,'healthy_after_seconds':time.time()-started,
                'graceful_shutdown':'engine_shutdown_complete' in logs,
                'lease_conflict':'Another worker owns' in logs,
                'checkpoint_restores':logs.count('checkpoint_restored'),'health':after}
        (output/'restart.log').write_text(logs);(output/'restart.json').write_text(json.dumps(result,indent=2))
        assert result['graceful_shutdown'] and not result['lease_conflict'] and result['checkpoint_restores']==after['services']['engine']['engines'],result
        print(json.dumps({k:v for k,v in result.items() if k!='health'}),flush=True)
    samples=[]
    for index in range(3):
        if index:time.sleep(30)
        resources=[json.loads(line) for line in docker('stats','--no-stream','--format','{{json .}}').splitlines()]
        samples.append({'health':health(),'resources':[row for row in resources if row['Name'].startswith('brokeweb-')]})
        print(f'Captured resource sample {index+1}/3',flush=True)
    query="""SELECT json_build_object(
      'database_bytes',pg_database_size(current_database()),
      'snapshots',(SELECT count(*) FROM setup_snapshots),
      'signals',(SELECT count(*) FROM signals),
      'bars',(SELECT count(*) FROM market_bars),
      'avg_snapshot_payload_bytes',(SELECT avg(pg_column_size(payload)) FROM setup_snapshots),
      'checkpoints',(SELECT json_agg(json_build_object('symbol',symbol,'compressed_bytes',pg_column_size(checkpoint_blob))) FROM setup_current),
      'table_sizes',(SELECT json_agg(json_build_object('table',relname,'bytes',pg_total_relation_size(relid))) FROM pg_catalog.pg_statio_user_tables)
    );"""
    db=json.loads(docker('compose','exec','-T','postgres','psql','-U','brokeweb','-d','brokeweb','-Atc',query))
    (output/'performance.json').write_text(json.dumps({'samples':samples,'database':db},indent=2))
    (output/'health.json').write_text(json.dumps(samples[-1]['health'],indent=2))
    (output/'compose-ps.txt').write_text(docker('compose','ps'))
    print(json.dumps({'status':'PASS','samples':len(samples),'database_bytes':db['database_bytes']}))


if __name__=='__main__':main()
