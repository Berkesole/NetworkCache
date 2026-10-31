"""Freeze the worker-c-only pilot after building and testing its isolated image."""
import hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v1');SRC=ROOT/'source'
assert subprocess.check_output(['hostname'],text=True).strip()=='simpleifz'
assert not subprocess.check_output(['docker','ps','-q'],text=True).strip()
BASE='rsync-router:ruikekkk-kathara';IMAGE='rsync-router:concurrency-pilot-v1'
base_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',BASE],text=True).strip()
assert base_id=='sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74'
subprocess.run(['docker','build','-f','Dockerfile.pilot','--build-arg','BASE_IMAGE='+BASE,'-t',IMAGE,'.'],cwd=SRC,check=True)
for target in ('test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor'):
 with (ROOT/(target+'.log')).open('w') as f:
  subprocess.run(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,IMAGE],stdout=f,stderr=subprocess.STDOUT,check=True)
subprocess.run(['python3','-m','unittest','discover','-s',str(SRC/'experiments/concurrency_pilot_v1'),'-p','test_pilot.py'],check=True)
scripts=SRC/'experiments/content_discovery/delay_find_content/scripts';sys.path.insert(0,str(scripts))
import generate_latency_v4_bf1024_h3_c512 as gen
gen.generate_one('kua-dc-v4-32','iid-zipf',65536,1,4096)
subprocess.run(['git','add','-A'],cwd=SRC,check=True)
subprocess.run(['git','commit','-qm','Add isolated open-arrival concurrency pilot and measurement hooks'],cwd=SRC,check=True)
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SRC,text=True).strip()
files=subprocess.check_output(['git','ls-files','-z'],cwd=SRC).decode().split('\0')
manifest={n:hashlib.sha256((SRC/n).read_bytes()).hexdigest() for n in files if n and (SRC/n).is_file()}
for p in gen.ROOT.rglob('*'):
 if p.is_file():manifest[str(p.relative_to(SRC))]=hashlib.sha256(p.read_bytes()).hexdigest()
record=dict(branch='codex/single-cluster-concurrency-pilot-v1',commit=head,worker='worker-c',
 image_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',IMAGE],text=True).strip(),
 base_image_id=base_id,sources=manifest,evidence='EXPLORATORY_PILOT',seed=1,repetitions=1,
 per_node_inflight=1,arrival_models=['poisson','burst'],traffic_metric='application frames excluding TCP/IP overhead',
 protocol_unchanged=['src/node.cpp','src/query_table.hpp','src/bloomfilter.hpp','src/union_window.hpp'],
 tests=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_pilot.py'])
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
if (ROOT/'campaign.json').exists():
 assert not list((ROOT/'raw').glob('*/attempt*')), 'cannot replace an executed campaign'
 old=json.loads((ROOT/'campaign.json').read_text());archive=ROOT/'baseline'/('unstarted-campaign-'+old['fingerprint']+'.json')
 (ROOT/'campaign.json').replace(archive)
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
print('FROZEN',record['fingerprint'],head,record['image_id'],flush=True)
