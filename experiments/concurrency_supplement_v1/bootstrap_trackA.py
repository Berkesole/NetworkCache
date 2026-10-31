#!/usr/bin/env python3
"""Track A stage 1: create the simple-station root, branch, and apply the semantic patches.

Runs on simple-station. Reproduces, on this machine's own port, the same changes Track B
applied on volcengine-3:
  * 5b/5c  federation warm-up gate + pre/post, per-slot query lifetime
  *        slots_in_parallel recording, UNIT derived from the root name
  *        federation measurement group sizes [4096, 7680]
  *        containers_for race tolerance, stop() timeout/exist_ok robustness
  *        generators: controlled rounds env, iid-zipf 1 KiB/1 MiB arms,
           federation PRE_BINS/POST_BINS env + asserts
  *        network_backend.py aligned byte-for-byte with the volcengine-3 copy
Changed tracked files are left diffable: `git diff` is written to recovery/.
"""
import difflib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/home/simpleai/zhengzihao/networkcache-concurrency-supplement-v1')
OLD = Path('/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5')
BRANCH = 'simple-station/concurrency-supplement-v1'
NET_BACKEND_VC3 = Path('/tmp/network_backend.vc3.py')

assert OLD.is_dir(), OLD
assert NET_BACKEND_VC3.is_file(), 'the volcengine-3 network_backend.py payload is missing'
if ROOT.exists():
    raise SystemExit(f'{ROOT} already exists; refusing to touch it')
ROOT.mkdir(parents=True)
(ROOT / 'recovery').mkdir()

subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(OLD / 'source'),
                str(ROOT / 'source')], check=True)
S = ROOT / 'source'
for key in ('user.name', 'user.email'):
    val = subprocess.check_output(['git', '-C', str(OLD / 'source'), 'config', key], text=True).strip()
    subprocess.run(['git', '-C', str(S), 'config', key, val], check=True)
base = subprocess.check_output(['git', '-C', str(S), 'rev-parse', 'HEAD'], text=True).strip()
subprocess.run(['git', '-C', str(S), 'checkout', '--quiet', '-b', BRANCH], check=True)
print('branch', BRANCH, 'from', base[:8], flush=True)

D = S / 'experiments/concurrency_slides_v5'
G = S / 'experiments/content_discovery/delay_find_content/scripts'


def patch(path, pairs, label):
    p = Path(path)
    s = p.read_text()
    for old, new, count in pairs:
        got = s.count(old)
        assert got == count, f'{label}: anchor x{got} != {count}: {old[:70]!r}'
        s = s.replace(old, new, count)
    p.write_text(s)
    print('patched', label, flush=True)


# ---------------- 5(c): per-slot query lifetime ----------------
HELPER = '''_T95_DF2=4.302652729911275
def _ols_slope_ci(xs,ys):
 n=len(xs)
 if n<3:return None
 mx=sum(xs)/n;my=sum(ys)/n
 sxx=sum((x-mx)**2 for x in xs);sxy=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
 if sxx==0:return None
 slope=sxy/sxx;intercept=my-slope*mx
 resid=[y-(intercept+slope*x) for x,y in zip(xs,ys)]
 dof=n-2
 if dof<=0:return None
 s2=sum(r*r for r in resid)/dof
 se=(s2/sxx)**0.5
 return dict(slope=slope,se=se,ci_low=slope-_T95_DF2*se,ci_high=slope+_T95_DF2*se,dof=dof,intercept=intercept)
def warmup_gate(ledger,bins=4):
 """Warm-up gate for federation: both domains local latency over the last pre
 bins must have an OLS slope whose 95% t CI contains 0. Returns the evidence."""
 pre=[r for r in ledger if r.get('phase')=='pre']
 evidence={};reasons=[]
 all_bins=sorted({int(r['bin']) for r in pre})
 use=all_bins[-bins:]
 for domain,(lo,hi) in (('A',(1,32)),('B',(33,64))):
  rows=[r for r in pre if lo<=int(r['requester'])<=hi]
  per=[]
  for b in use:
   sel=[int(r['total_latency_ns']) for r in rows if int(r['bin'])==b and r.get('status')=='ok' and r.get('total_latency_ns')]
   if sel:per.append((b,sum(sel)/len(sel)))
  if len(per)<3:
   reasons.append(f'domain{domain}:insufficient_pre_bins={len(per)}');continue
  fit=_ols_slope_ci([q[0] for q in per],[q[1] for q in per])
  if fit is None:
   reasons.append(f'domain{domain}:degenerate');continue
  passed=fit['ci_low']<=0<=fit['ci_high']
  evidence[domain]=dict(bins=[q[0] for q in per],bin_mean_ns={str(q[0]):q[1] for q in per},
   plateau_ns=sum(q[1] for q in per)/len(per),slope_ns_per_bin=fit['slope'],se_ns_per_bin=fit['se'],
   ci_low_ns_per_bin=fit['ci_low'],ci_high_ns_per_bin=fit['ci_high'],dof=fit['dof'],contains_zero=passed)
  if not passed:reasons.append(f'domain{domain}:slope_ci_excludes_zero')
 return dict(passed=not reasons,reasons=reasons,bins_used=use,domains=evidence)
'''
TRIGGER_OLD = ("    if config['family']=='federation' and i==2:\n"
               "     import federation\n"
               "     bstart=time.monotonic_ns();probe_ids=federation.boundary(control,raw,MAPPED,"
               "config['method'],digest,health,lab/'shared/traces');"
               "timing['boundary_seconds']=(time.monotonic_ns()-bstart)/1e9")
TRIGGER_NEW = ("    if config['family']=='federation' and i==2:\n"
               "     gstart=time.monotonic_ns();gate=warmup_gate(ledger);"
               "ctl.dump(raw/'warmup_gate.json',gate);"
               "timing['warmup_gate_seconds']=(time.monotonic_ns()-gstart)/1e9;"
               "timing['warmup_gate_passed']=gate['passed']\n"
               "     if not gate['passed']:\n"
               "      result=dict(status='warmup_gate_failed',reason=';'.join(gate['reasons']));break\n"
               "     import federation\n"
               "     bstart=time.monotonic_ns();probe_ids=federation.boundary(control,raw,MAPPED,"
               "config['method'],digest,health,lab/'shared/traces');"
               "timing['boundary_seconds']=(time.monotonic_ns()-bstart)/1e9;timing['activation_ok']=True")

patch(D / 'pilot.py', [
    ("protocol_query_lifetime_seconds=30,origin_fallback_seconds=1.5,",
     "protocol_query_lifetime_seconds=30,protocol_query_lifetime_overrides=[7],"
     "origin_fallback_seconds=1.5,", 1),
    (" assert query_lifetime_seconds==30 and loop_sleep_ms==25",
     " assert query_lifetime_seconds in (30,7) and loop_sleep_ms==25", 1),
    ("def peak(rows,per_node=False):", HELPER + "def peak(rows,per_node=False):", 1),
    (TRIGGER_OLD, TRIGGER_NEW, 1),
    ("  assert list(map(len,groups))==[1024,2560]",
     "  assert list(map(len,groups))==[4096,7680]", 1),
    ("UNIT='networkcache-concurrency-slides-v5'", "UNIT=ROOT.name", 1),
    ("  result.update(config=config,started_mono_ns=begin,measurement_finished_mono_ns="
     "time.monotonic_ns(),timing=timing,requests_issued=any((control/'commands').glob('node*/*.tsv')))",
     "  result.update(config=config,started_mono_ns=begin,measurement_finished_mono_ns="
     "time.monotonic_ns(),timing=timing,requests_issued=any((control/'commands').glob('node*/*.tsv')))"
     "\n  result['slots_in_parallel']=int(os.environ.get('RSYNC_SLOTS_IN_PARALLEL','0')) or None", 1),
    (""" ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=json.loads(out(['docker','inspect',*ids])) if ids else []""",
     """ ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=[]
 for cid in ids:
  probe=subprocess.run(['docker','inspect',cid],capture_output=True,text=True)
  if probe.returncode==0:allc+=json.loads(probe.stdout)""", 1),
    ("""   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=True,timeout=60)
    after=json.loads(out(['docker','inspect',*[c['Id'] for c in current]]));ctl.dump(raw/'containers.after.json',after)
    if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')
    if result['status']=='ok' and len(list((control/'stopped').glob('node*.stopped')))!=len(NODES):raise RuntimeError('graceful stop acknowledgement missing')""",
     """   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=False,timeout=600)
   remaining=containers_for(lab)
   after=json.loads(out(['docker','inspect',*[c['Id'] for c in remaining]])) if remaining else []
   ctl.dump(raw/'containers.after.json',after)
   if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')
   if result['status']=='ok' and len(list((control/'stopped').glob('node*.stopped')))!=len(NODES):raise RuntimeError('graceful stop acknowledgement missing')""", 1),
], 'pilot.py')

# ---------------- generators ----------------
patch(G / 'generate_latency_v4_bf1024_h3_c512.py', [
    ("CONTROLLED_ROUNDS = 8",
     "CONTROLLED_ROUNDS = int(os.environ.get(\"LATENCY_V4_CONTROLLED_ROUNDS\", \"8\"))", 1),
    ("            expected = 256 if workload == \"controlled\" else iid_requests",
     "            expected = CONTROLLED_ROUNDS * 32 if workload == \"controlled\" else iid_requests", 1),
    ("                for group in range(1, 9 if workload == \"controlled\" else iid_requests // 128 + 1):",
     "                for group in range(1, (CONTROLLED_ROUNDS + 1) if workload == \"controlled\" else iid_requests // 128 + 1):", 1),
    ("    rows += [(\"kua-dc-v4-32\", \"controlled\", size) for size in (1024, 1048576)]",
     "    rows += [(\"kua-dc-v4-32\", \"controlled\", size) for size in (1024, 1048576)]\n"
     "    rows += [(\"kua-dc-v4-32\", \"iid-zipf\", size) for size in (1024, 1048576)]", 1),
], 'generate_latency_v4')

patch(G / 'generate_federation_v3_pilot.py', [
    ("from pathlib import Path", "import os\nfrom pathlib import Path", 1),
    ("ZIPF_ALPHA = 1.1",
     "ZIPF_ALPHA = 1.1\nPRE_BINS = int(os.environ.get(\"FEDERATION_PRE_BINS\", \"8\"))\n"
     "POST_BINS = int(os.environ.get(\"FEDERATION_POST_BINS\", \"16\"))", 1),
    ("    for bin_index in range(1, 9):", "    for bin_index in range(1, PRE_BINS + 1):", 1),
    ("for bin_index in range(1, 17):", "for bin_index in range(1, POST_BINS + 1):", 2),
    ("        assert len(rows) == 3584",
     "        assert len(rows) == PRE_BINS * 128 + POST_BINS * 160", 1),
    ("        assert sum(row[\"phase\"] == \"pre\" for row in rows) == 1024",
     "        assert sum(row[\"phase\"] == \"pre\" for row in rows) == PRE_BINS * 128", 1),
    ("        assert sum(row[\"request_class\"] == \"remote\" for row in rows) == 2048",
     "        assert sum(row[\"request_class\"] == \"remote\" for row in rows) == POST_BINS * 128", 1),
    ("        assert sum(row[\"request_class\"] == \"local_control\" for row in rows) == 512",
     "        assert sum(row[\"request_class\"] == \"local_control\" for row in rows) == POST_BINS * 32", 1),
    ("        assert local_index == {\"A\": 16, \"B\": 16}\n    assert len(rows) == 3584",
     "        assert local_index == {\"A\": 16, \"B\": 16}\n"
     "    assert len(rows) == PRE_BINS * 128 + POST_BINS * 160", 1),
], 'generate_federation_v3_pilot')

patch(G / 'generate_federation_v4_bf1024_h3_formal.py', [
    ("            assert len(rows) == 3584",
     "            assert len(rows) == timeline.PRE_BINS * 128 + timeline.POST_BINS * 160", 1),
    ("            assert sum(row[\"phase\"] == \"pre\" for row in rows) == 1024",
     "            assert sum(row[\"phase\"] == \"pre\" for row in rows) == timeline.PRE_BINS * 128", 1),
    ("            assert sum(row[\"request_class\"] == \"remote\" for row in rows) == 2048",
     "            assert sum(row[\"request_class\"] == \"remote\" for row in rows) == timeline.POST_BINS * 128", 1),
    ("            assert sum(row[\"request_class\"] == \"local_control\" for row in rows) == 512",
     "            assert sum(row[\"request_class\"] == \"local_control\" for row in rows) == timeline.POST_BINS * 32", 1),
], 'generate_federation_v4_formal')

# ---------------- align the reused module byte-for-byte ----------------
shutil.copy2(NET_BACKEND_VC3, D / 'network_backend.py')
print('aligned network_backend.py to the volcengine-3 bytes', flush=True)

diff = subprocess.run(['git', '-C', str(S), 'diff'], capture_output=True, text=True).stdout
(ROOT / 'recovery' / 'trackA-changes.patch').write_text(diff)
print('changed files:', len([l for l in diff.splitlines() if l.startswith('diff --git')]),
      '| patch bytes:', len(diff), flush=True)
for name in ('pilot.py',):
    compile((D / name).read_text(), name, 'exec')
print('pilot.py compiles', flush=True)
print('STAGE1_OK', flush=True)