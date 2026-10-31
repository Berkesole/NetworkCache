"""Worker-c v2 clone and exact-lab recovery. Never edits old raw or old source."""
import argparse,collections,csv,hashlib,json,pathlib,shutil,socket,subprocess,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v2');OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v1')
BRANCH='codex/single-cluster-concurrency-pilot-v2'
def out(a):return subprocess.check_output(a,text=True).strip()
def hashes(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}
def recover():
 assert socket.gethostname()=='simpleifz'
 rec=ROOT/'recovery';rec.mkdir(parents=True,exist_ok=True)
 if (rec/'complete.json').exists():return json.loads((rec/'complete.json').read_text())
 assert out(['systemctl','show','networkcache-concurrency-pilot-v1','-p','ActiveState','--value']) in ('failed','inactive')
 sub=pathlib.Path('02-closed-r0-c4-nc/attempt001');raw=OLD/'raw-bootstrap-v2'/sub;lab=OLD/'lab-bootstrap-v2'/sub
 control=lab/'shared/concurrency-pilot-v1/control'
 rows=[]
 for name,expected in (('setup',32),('measurement-1',256)):
  result=json.loads((raw/name/'result.json').read_text());assert result['status']=='ok' and result['completed']==expected
  with (raw/name/'requests.csv').open() as h:rows+=list(csv.DictReader(h))
 audit=collections.Counter(line.split('\t')[0] for p in (control/'delivery-audit').glob('*.tsv') for line in p.read_text().splitlines())
 assert len(rows)==288 and all(x['status']=='ok' for x in rows)
 assert set(audit)=={x['request_index'] for x in rows} and all(v==1 for v in audit.values())
 assert len(list((control/'stopped').glob('node*.stopped')))==32
 ids=out(['docker','ps','-q']).splitlines()
 if ids:
  containers=json.loads(out(['docker','inspect',*ids]));assert len(containers)==44
  for c in containers:
   assert c['Config']['Labels'].get('lab_hash')=='EWc9DGFIJtQs8RrbXMIA'
   assert any(m['Source']==str(lab/'shared') for m in c['Mounts']),c['Name']
  (rec/'containers.before.json').write_text(json.dumps(containers,indent=2)+'\n')
 before=hashes(OLD/'raw-bootstrap-v2');(rec/'old-raw-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
 if not (rec/'lab_snapshot').exists():shutil.copytree(lab,rec/'lab_snapshot')
 with (rec/'cleanup.log').open('a') as h:
  subprocess.run(['kathara','lclean','-d',str(lab)],stdout=h,stderr=subprocess.STDOUT,check=True,timeout=120)
 assert not out(['docker','ps','-q'])
 assert hashes(OLD/'raw-bootstrap-v2')==before
 record=dict(status='RECOVERED_MEASUREMENT_ONLY',measurement=256,setup=32,audit=288,
  old_raw_unchanged=True,cleanup_complete=True,recovered_epoch_ns=time.time_ns(),snapshot_sha256=hashes(rec/'lab_snapshot'),
  note='Old top-level result and archive remain incomplete; no old raw file was repaired. Do not count as v2 smoke.')
 (rec/'complete.json').write_text(json.dumps(record,indent=2)+'\n');return record
def prepare():
 assert socket.gethostname()=='simpleifz'
 ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir()
 old=json.loads((OLD/'campaign.json').read_text());assert old['fingerprint']=='c9980d22e1dfec8e579826ad119906b4ba159c21c5f0fb9b60bc0ba1a1f7a1f2'
 assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
 for n,d in old['sources'].items():assert hashlib.sha256((OLD/'source'/n).read_bytes()).hexdigest()==d,n
 shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
 subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(ROOT/'source')],check=True)
 subprocess.run(['git','-C',str(ROOT/'source'),'checkout','-b',BRANCH],check=True)
 for name in old['sources']:
  p=ROOT/'source'/name
  if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
 return recover()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','recover']);p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:p.error('requires --execute')
 result=prepare() if a.command=='prepare' else recover();print(json.dumps({k:v for k,v in result.items() if k!='snapshot_sha256'},indent=2))
