"""Create isolated worker-c v4. No writes to previous sources, raw, or images."""
import hashlib,json,pathlib,shutil,socket,subprocess,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v4')
OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v2')
def out(a):return subprocess.check_output(a,text=True).strip()
def recover():
 return json.loads((ROOT/'recovery/complete.json').read_text())
def prepare():
 assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
 assert out(['systemctl','show','networkcache-concurrency-pilot-v2','-p','ActiveState','--value'])=='inactive'
 assert out(['systemctl','show','networkcache-concurrency-pilot-v3-gate','-p','ActiveState','--value']) in ('failed','inactive')
 assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
 old=json.loads((OLD/'campaign.json').read_text())
 assert old['fingerprint']=='4d7d9a015f4481710a45ee64af3a5a46f964a79c885994b55a7d6b85f15cdecf'
 assert shutil.disk_usage(OLD).free>=100*1024**3
 for name,digest in old['sources'].items():assert hashlib.sha256((OLD/'source'/name).read_bytes()).hexdigest()==digest,name
 ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir()
 shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
 subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(ROOT/'source')],check=True)
 subprocess.run(['git','-C',str(ROOT/'source'),'checkout','-b','codex/single-cluster-concurrency-pilot-v4'],check=True)
 for key in ('user.name','user.email'):
  value=out(['git','-C',str(OLD/'source'),'config',key]);subprocess.run(['git','-C',str(ROOT/'source'),'config',key,value],check=True)
 for name in old['sources']:
  p=ROOT/'source'/name
  if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
 record=dict(cleanup_complete=True,cleanup_action='none; parent already idle with zero containers',parent_commit=out(['git','-C',str(OLD/'source'),'rev-parse','HEAD']),epoch_ns=time.time_ns())
 (ROOT/'recovery/complete.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps(record),flush=True)
if __name__=='__main__':prepare()
