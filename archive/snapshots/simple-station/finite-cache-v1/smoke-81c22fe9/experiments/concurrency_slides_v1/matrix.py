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
def slot(c,stage,seed=1,rep=1):
 return dict(c,stage=stage,seed=seed,repetition=rep,id=f"{c['condition']}--s{seed:03d}-r{rep}",arrival_seed=9172+seed)
def slots(stage):
 cs=conditions()
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
def request_count(c):return 3584 if c['family']=='federation' else 4096 if c['family'] in ('zipf','uniform') else 256
