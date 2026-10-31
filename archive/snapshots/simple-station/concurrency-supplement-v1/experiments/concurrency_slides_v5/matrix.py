"""Immutable condition/slot enumeration, no I/O."""
import random
def conditions():
 out=[]
 for family,hosts,sizes,catalogs,distributions,models in (
  ('controlled',(16,32),(65536,),(512,),('controlled',),('poisson',)),
  ('zipf',(16,32),(65536,),(512,),('iid-zipf',),('poisson','burst')),
  ('uniform',(16,32),(65536,),(512,),('iid-uniform',),('poisson','burst')),
  ('size',(32,),(1024,1048576),(512,),('controlled',),('poisson',)),
  ('federation',(64,),(65536,),(512,1000),('zipf','uniform'),('poisson',))):
  for host in hosts:
   for size in sizes:
    for catalog in catalogs:
     for distribution in distributions:
      for rate in (32,128):
       for model in models:
        group=len(out)//2
        for method in (('nc','tcp-direct') if group%2==0 else ('tcp-direct','nc')):
         ident=f'{family}-n{host}-b{size}-c{catalog}-{distribution}-{model}-r{rate}-{method}'
         out.append(dict(condition=ident,family=family,hosts=host,size=size,catalog=catalog,distribution=distribution,model=model,rate=rate,method=method,
          stage='load',query_lifetime_seconds=30,loop_sleep_ms=25,reuse_connections=method=='nc',request_timeout_abort=False))
 assert len(out)==64 and len({c['condition'] for c in out})==64
 return out
def supplement_v2_conditions():
    """The 18 conditions of the dual-track v2 campaign (supplementary_slots_v2.csv).

    Conditions shared with the lineage are returned with the lineage field values, so a
    v2 slot equals its lineage counterpart and membership checks hold either way.
    """
    lineage = {c['condition']: c for c in conditions()}
    out = []

    def add(family, hosts, size, catalog, distribution, method, rate=128, lifetime=30,
            suffix=''):
        ident = (f'{family}-n{hosts}-b{size}-c{catalog}-{distribution}-poisson-r{rate}-'
                 f'{method}{suffix}')
        shared = lineage.get(ident)
        if shared is not None:
            out.append(shared)
            return
        out.append(dict(condition=ident, family=family, hosts=hosts, size=size,
                        catalog=catalog, distribution=distribution, model='poisson',
                        rate=rate, method=method, stage='load',
                        query_lifetime_seconds=lifetime, loop_sleep_ms=25,
                        reuse_connections=method == 'nc', request_timeout_abort=False))

    for method in ('nc', 'tcp-direct'):
        add('controlled', 32, 65536, 512, 'controlled', method)
    for size in (1024, 65536, 1048576):
        for method in ('nc', 'tcp-direct'):
            add('size', 32, size, 512, 'iid-zipf', method, rate=32)
    for method in ('nc', 'tcp-direct'):
        add('size', 32, 65536, 512, 'iid-zipf', method)
    for method in ('nc', 'tcp-direct'):
        add('federation', 64, 65536, 512, 'zipf', method)
    for method in ('nc', 'tcp-direct'):
        add('size', 32, 65536, 512, 'iid-zipf', method, lifetime=7, suffix='-lt7s')
    for family, distribution in (('zipf', 'iid-zipf'), ('uniform', 'iid-uniform')):
        for method in ('nc', 'tcp-direct'):
            add(family, 32, 65536, 512, distribution, method)
    assert len(out) == 18 and len({c['condition'] for c in out}) == 18
    return out


ORDER_SEED_V2 = "concurrency-supplement-v2:order-v1"


def slot(c,stage,seed=1,rep=1):
 return dict(c,stage=stage,seed=seed,repetition=rep,id=f"{c['condition']}--s{seed:03d}-r{rep}",arrival_seed=9172+seed)
def slots(stage):
 cs=conditions()
 if stage=='supplement_v2':
  out=[slot(c,stage,seed,1) for c in supplement_v2_conditions() for seed in range(1,11)]
  random.Random(ORDER_SEED_V2).shuffle(out)
  assert len(out)==180 and len({c['id'] for c in out})==180
  return out
 if stage=='smoke':
  out=[]
  for n in (16,32,64):
   for method in ('nc','tcp-direct'):
    c=next(c for c in cs if c['hosts']==n and c['method']==method and c['family']==('federation' if n==64 else 'zipf') and c['rate']==32 and c['model']=='poisson' and c['catalog']==512)
    out.append(slot(c,'smoke'))
  return out
 if stage=='pilot':return [slot(c,stage) for c in cs]
 if stage!='formal':raise ValueError(stage)
 out=[]
 for family in ('controlled','zipf','uniform','size','federation'):
  pairs=[cs[i:i+2] for i in range(0,len(cs),2) if cs[i]['family']==family]
  for seed in range(1,11):
   for rep in (1,2):
    groups=list(pairs);random.Random(f'concurrency-slides-v1:{family}:{seed}:{rep}').shuffle(groups)
    for index,pair in enumerate(groups):
     ordered=sorted(pair,key=lambda c:c['method'],reverse=(seed+rep+index)%2==1)
     out.extend(slot(c,stage,seed,rep) for c in ordered)
 assert len(out)==1280 and len({c['id'] for c in out})==1280
 return out
def request_count(c):
 if c['family']=='federation':return 11776
 return 4096
