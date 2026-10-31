"""Freeze Linux bridge/veth backend; never reconfigure a running lab here."""
import hashlib,json,pathlib,re,subprocess
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def validate_qdisc(opts):
 assert set(opts)<= {'limit','delay','rate','ecn','gap'},'unexpected netem impairment'
 assert opts['limit']==1000 and not opts.get('ecn',False) and opts.get('gap',0)==0
 delay=opts['delay'];rate=opts['rate']
 assert isinstance(delay,dict) and isinstance(rate,dict),'unexpected tc JSON schema'
 assert set(delay)<= {'delay','jitter','correlation'} and set(rate)<= {'rate','packetoverhead','cellsize','celloverhead'}
 assert abs(delay['delay']-.002)<1e-9 and delay.get('jitter',0)==0 and delay.get('correlation',0)==0
 assert rate['rate']==125000000 and all(rate.get(k,0)==0 for k in ('packetoverhead','cellsize','celloverhead'))
def point_to_point(text):
 lans={}
 for node,index,lan in re.findall(r'^(node\d+)\[(\d+)\]\s*=\s*([^\s#]+)',text,re.M):
  lans.setdefault(lan,[]).append((node,index))
 assert lans and all(len(v)==2 and len(set(v))==2 for v in lans.values()),'only point-to-point collision domains allowed'
 return lans
def preflight(root):
 record=json.loads((root/'backend.json').read_text())
 plugin=json.loads(out(['docker','plugin','inspect',record['plugin_name']]))[0]
 assert plugin['Id']==record['plugin_id'] and plugin['Enabled']
 assert sha(root/'kathara.conf')==record['config_sha256']
 assert sha(pathlib.Path(record['global_config_path']))==record['global_config_sha256'],'global Kathara settings changed'
 assert sha(root/'bin/kathara')==record['entrypoint_sha256']
 assert pathlib.Path(out(['which','kathara'])).resolve()==root/'bin/kathara','campaign wrapper not in PATH'
 return record
def prepare_lab(raw,containers):
 """Disable segmentation/coalescing only on this lab's veth pairs.

 This preserves packet-sized netem semantics instead of allowing backend-specific
 super-packets to bypass the intended MTU/packet queue model.
 """
 import controller
 host_links={x['ifindex']:x for x in json.loads(out(['ip','-d','-j','link']))}
 operations=[]
 for c in containers:
  cmd=['nsenter','-t',str(c['State']['Pid']),'-n','--']
  for link in json.loads(out(cmd+['ip','-d','-j','link'])):
   if not link['ifname'].startswith('eth'):continue
   assert link['linkinfo']['info_kind']=='veth'
   peer=host_links[link['link_index']]
   assert peer['linkinfo']['info_kind']=='veth'
   for prefix,dev in ((cmd,link['ifname']),([],peer['ifname'])):
    before=out(prefix+['ethtool','-k',dev])
    out(prefix+['ethtool','-K',dev,'gso','off','gro','off','tso','off'])
    after=out(prefix+['ethtool','-k',dev])
    for feature in ('generic-segmentation-offload','generic-receive-offload','tcp-segmentation-offload'):
     assert re.search(r'^'+feature+r': off(?: \[fixed\])?$',after,re.M),feature
    operations.append(dict(node=c['Config']['Labels']['name'],device=dev,namespace='container' if prefix else 'host-peer',before=before,after=after))
 controller.dump(raw/'offload_snapshot.json',dict(policy='gso/gro/tso off on exact lab veth pairs; packet-sized netem',interfaces=operations))
def verify_lab(root,lab,raw,containers):
 import controller
 # Diagnostics may use a sub-root, but all use the same immutable backend.
 record=preflight(root)
 lans=point_to_point((lab/'lab.conf').read_text())
 networks={network['NetworkID'] for c in containers for network in c['NetworkSettings']['Networks'].values()}
 inspected=json.loads(out(['docker','network','inspect',*sorted(networks)]))
 assert len(inspected)==len(lans)
 assert all(n['Driver']==record['plugin_name'] and len(n['Containers'])==2 for n in inspected)
 states=[]
 for c in containers:
  cmd=['nsenter','-t',str(c['State']['Pid']),'-n','--']
  links=json.loads(out(cmd+['ip','-d','-j','link']))
  qdisc=json.loads(out(cmd+['tc','-s','-j','qdisc']))
  for link in links:
   if not link['ifname'].startswith('eth'):continue
   assert link['linkinfo']['info_kind']=='veth' and link['mtu']==1500
   q=next(q for q in qdisc if q['dev']==link['ifname'] and q['kind']=='netem')
   validate_qdisc(q['options'])
  states.append(dict(node=c['Config']['Labels']['name'],links=links,qdisc=qdisc))
 controller.dump(raw/'backend_snapshot.json',dict(backend=record,point_to_point_lans=lans,networks=inspected,interfaces=states))
