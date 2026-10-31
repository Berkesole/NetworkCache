"""Single bridge regression, excluded from selection. Never auto-retry."""
import json,pathlib,hashlib,subprocess,shutil,os,sys,time
import pilot,matrix,reports,controller,network_backend
ROOT=pilot.ROOT
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert not out(['docker','ps','-q'])
 os.environ['PATH']=str(ROOT/'bin')+':'+os.environ['PATH']
 campaign=json.loads((ROOT/'campaign.json').read_text())
 assert out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE])==campaign['image_id']
 for n,d in campaign['sources'].items():assert sha(pilot.SOURCE/n)==d,n
 for n,d in campaign['inputs'].items():assert sha(ROOT/'inputs'/n)==d,n
 assert sha(ROOT/'backend.json')==campaign['backend_sha256']
 network_backend.preflight(ROOT)
 diag=ROOT/'diagnostics/bridge-regression';diag.mkdir(parents=True,exist_ok=False)
 (diag/'inputs').symlink_to(ROOT/'inputs',target_is_directory=True);shutil.copy2(ROOT/'campaign.json',diag/'campaign.json')
 pilot.ROOT=diag;pilot.LAB=diag/'lab';pilot.DERIVED=diag/'derived'
 sys.path.append('/data/networkcache-concurrency-slides-v2/source/experiments/concurrency_slides_v2')
 from observer import Observer
 old_health=pilot.Health;observers=[]
 class Health(old_health):
  def __init__(self,cs,method,helpers,raw):
   super().__init__(cs,method,helpers,raw);observers.append(Observer(cs,raw))
 pilot.Health=Health;original=pilot.finalize
 def finalize(raw,result,stop,archive,clean):
  errors=[]
  for observer in observers:
   try:observer.finish()
   except Exception as e:errors.append(repr(e))
  if errors:controller.dump(raw/'observer-errors.json',errors)
  return original(raw,result,stop,archive,clean)
 pilot.finalize=finalize
 conf=next(c for c in matrix.slots('pilot') if c['family']=='uniform' and c['hosts']==16 and c['rate']==128 and c['method']=='nc' and c['model']=='poisson')
 print('BRIDGE_REGRESSION_START',conf['id'],flush=True)
 result=pilot.one(conf,1);raw=diag/'raw/pilot'/conf['id']/'attempt001'
 summary=reports.diagnose(raw,diag);gate=reports.validate(raw,diag)
 no_observer_error=(raw/'observer_status.json').exists() and not json.loads((raw/'observer_status.json').read_text())['errors'] and not (raw/'observer-errors.json').exists()
 qualified=gate['qualified'] and no_observer_error
 controller.dump(ROOT/'recovery/target_regression.json',dict(qualified=qualified,gate=gate,raw=str(raw),campaign=campaign['fingerprint'],result_sha256=sha(raw/'result.json')))
 print(json.dumps(dict(result=result,summary=summary,qualified=qualified)),flush=True)
 assert not out(['docker','ps','-q'])
 controller.dump(ROOT/'recovery/complete.json',dict(cleanup_complete=qualified,target_regression_qualified=qualified,raw=str(raw),result_sha256=sha(raw/'result.json'),epoch_ns=time.time_ns()))
 if not qualified:raise RuntimeError('backend candidate failed; campaign remains blocked')
if __name__=='__main__':main()
