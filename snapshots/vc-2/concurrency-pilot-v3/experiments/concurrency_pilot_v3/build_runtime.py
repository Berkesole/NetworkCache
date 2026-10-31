import hashlib,json,pathlib,socket,subprocess,time
r=pathlib.Path('/data/networkcache-concurrency-pilot-v3');s=r/'source';image='rsync-router:concurrency-pilot-v3'
def out(a):return subprocess.check_output(a,text=True).strip()
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert out(['git','-C',str(s),'branch','--show-current'])=='codex/single-cluster-concurrency-pilot-v3'
old=json.loads((r/'baseline/parent_campaign.json').read_text())
allowed=['src/node.cpp','src/network/network_interface.hpp','src/network/tcp_network.hpp','src/network/tcp_network.cpp','tests/test_runtime_neighbor.cpp']
for name,digest in old['sources'].items():
 if name not in allowed:assert hashlib.sha256((s/name).read_bytes()).hexdigest()==digest,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v2'])==old['image_id']
subprocess.run(['git','-C',str(s),'add','--',*allowed],check=True)
subprocess.run(['git','-C',str(s),'commit','-qm','Enforce entry inbox snapshot to prevent live receive loop starvation'],check=True)
subprocess.run(['docker','build','-f','Dockerfile.pilot','--build-arg','BASE_IMAGE=rsync-router:concurrency-pilot-v2','-t',image,'.'],cwd=s,check=True)
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor']
for target in targets:
 with (r/(target+'.log')).open('w') as h:subprocess.run(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,image],stdout=h,stderr=subprocess.STDOUT,check=True)
record=dict(image_id=out(['docker','image','inspect','--format','{{.Id}}',image]),runtime_commit=out(['git','-C',str(s),'rev-parse','HEAD']),
 runtime_inputs={name:hashlib.sha256((s/name).read_bytes()).hexdigest() for name in old['sources'] if not name.startswith('experiments/')},tests=targets,epoch_ns=time.time_ns())
(r/'runtime_build.json').write_text(json.dumps(record,indent=2)+'\n');print('RUNTIME_TESTS_PASSED',record['image_id'],flush=True)
