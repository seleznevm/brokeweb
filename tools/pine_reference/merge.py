"""Merge chart-export plot groups by UTC time, never by row number."""
import argparse,csv
from pathlib import Path

def merge(paths,output):
    combined={};times=None
    for path in paths:
        with Path(path).open(encoding='utf-8-sig',newline='') as handle:rows=list(csv.DictReader(handle))
        seen=set()
        for row in rows:
            normalized={('PARITY_'+key.split('PARITY_',1)[1] if 'PARITY_' in key else key.lower() if key.lower() in ('time','open','high','low','close','volume') else key):value for key,value in row.items()}
            stamp=normalized.get('time')
            if not stamp or stamp in seen:raise ValueError(f'{path}: missing/duplicate time')
            seen.add(stamp);target=combined.setdefault(stamp,{})
            for key,value in normalized.items():
                if key in target and target[key]!=value:raise ValueError(f'Conflicting {key} at {stamp}')
                target[key]=value
        if times is not None and times!=seen:raise ValueError('Export groups have different timestamp coverage')
        times=seen
    if not combined:raise ValueError('No reference rows')
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    columns=['time']+sorted({key for row in combined.values() for key in row}-{'time'})
    with path.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader()
        for stamp in sorted(combined):writer.writerow(combined[stamp])
    return len(combined)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('inputs',nargs='+');parser.add_argument('--output',required=True);args=parser.parse_args();print(merge(args.inputs,args.output))
