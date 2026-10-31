"""One explicit failed-condition regression; never enters campaign selection."""
import pathlib,json,shutil,subprocess,hashlib,time
import pilot,matrix,reports,controller
from observer import Observer
ROOT=pilot.ROOT
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert not out(['docker','ps','-q'])
 campaign=json.loads((ROOT/'campaign.json').read_text())
 assert out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE])==campaign['image_id']
 for name,d in campaign['sources'].items():assert sha(pilot.SOURCE/name)==d,name
 for name,d in campaign['inputs'].items():assert sha(ROOT/'inputs'/name)==d,name
 diag=ROOT/'diagnostics/control-data-lanes';diag.mkdir(exist_ok=False);(diag/'inputs').symlink_to(ROOT/'inputs',target_is_directory=True)
 shutil.copy2(ROOT/'campaign.json',diag/'campaign.json')
 pilot.ROOT=diag;pilot.LAB=diag/'lab';pilot.DERIVED=diag/'derived'
 old_health=pilot.Health;observers=[]
 class Health(old_health):
  def __init__(self,cs,method,helpers,raw):
   super().__init__(cs,method,helpers,raw);observers.append(Observer(cs,raw))
 pilot.Health=Health
 original=pilot.finalize
 def finalize(raw,result,stop,archive,clean):
  errors=[]
  for observer in observers:
   try:observer.finish()
   except Exception as e:errors.append(repr(e))
  if errors:controller.dump(raw/'observer-errors.json',errors)
  return original(raw,result,stop,archive,clean)
 pilot.finalize=finalize
 conf=next(c for c in matrix.slots('pilot') if c['family']=='uniform' and c['hosts']==16 and c['rate']==128 and c['method']=='nc' and c['model']=='poisson')
 print('REGRESSION_START',conf['id'],flush=True)
 result=pilot.one(conf,1);raw=diag/'raw/pilot'/conf['id']/'attempt001';summary=reports.diagnose(raw,diag)
 gate=reports.validate(raw,diag);observer=json.loads((raw/'observer_status.json').read_text())
 qualified=gate['qualified'] and not observer['errors'] and not (raw/'observer-errors.json').exists()
 controller.dump(ROOT/'recovery/target_regression.json',dict(qualified=qualified,gate=gate,raw=str(raw),campaign=campaign['fingerprint'],result_sha256=sha(raw/'result.json'),observer=observer))
 print(json.dumps(dict(result=result,summary=summary,transport=json.loads((reports.destination(raw,diag)/'transport_summary.json').read_text()),qualified=qualified)),flush=True)
 assert not out(['docker','ps','-q'])
 if not qualified:raise RuntimeError('target regression failed; do not launch campaign')
 controller.dump(ROOT/'recovery/complete.json',dict(cleanup_complete=True,target_regression_qualified=True,raw=str(raw),result_sha256=sha(raw/'result.json'),epoch_ns=time.time_ns()))
if __name__=='__main__':main()
