"""Independent descriptive statistics. Never modifies Pine Research Mode samples."""
from collections import defaultdict

def summarize(samples:list[dict],group_by='family'):
    groups=defaultdict(list)
    for sample in samples: groups[str(sample.get(group_by,'unknown'))].append(sample)
    return [{'group':name,'count':len(rows),'completed':sum(bool(r.get('completed',False)) for r in rows),'t1_hits':sum(r.get('t1_hit') is True for r in rows),'sl_hits':sum(r.get('sl_hit') is True for r in rows)} for name,rows in groups.items()]
