#!/usr/bin/env python3
"""Apply the supplementary campaign changes (5b warm-up gate, 5c lifetime) to pilot.py."""
from pathlib import Path
import sys

P = Path(sys.argv[1])
s = P.read_text()


def rep(text, old, new, n=1):
    got = text.count(old)
    assert got == n, f"anchor x{got} != {n}: {old[:80]!r}"
    return text.replace(old, new, n)


# ---------------- 5(c): per-slot query lifetime override ----------------
s = rep(s, "protocol_query_lifetime_seconds=30,origin_fallback_seconds=1.5,",
        "protocol_query_lifetime_seconds=30,protocol_query_lifetime_overrides=[7],"
        "origin_fallback_seconds=1.5,")
s = rep(s, " assert query_lifetime_seconds==30 and loop_sleep_ms==25",
        " assert query_lifetime_seconds in (30,7) and loop_sleep_ms==25")

# ---------------- 5(b): warm-up gate helper ----------------
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
 """Warm-up gate for federation: both domains\' local latency over the last pre
 bins must have an OLS slope whose 95% t CI contains 0. Returns the evidence."""
 pre=[r for r in ledger if r.get(\'phase\')==\'pre\']
 evidence={};reasons=[]
 all_bins=sorted({int(r[\'bin\']) for r in pre})
 use=all_bins[-bins:]
 for domain,(lo,hi) in ((\'A\',(1,32)),(\'B\',(33,64))):
  rows=[r for r in pre if lo<=int(r[\'requester\'])<=hi]
  per=[]
  for b in use:
   sel=[int(r[\'total_latency_ns\']) for r in rows if int(r[\'bin\'])==b and r.get(\'status\')==\'ok\' and r.get(\'total_latency_ns\')]
   if sel:per.append((b,sum(sel)/len(sel)))
  if len(per)<3:
   reasons.append(f\'domain{domain}:insufficient_pre_bins={len(per)}\');continue
  fit=_ols_slope_ci([q[0] for q in per],[q[1] for q in per])
  if fit is None:
   reasons.append(f\'domain{domain}:degenerate\');continue
  passed=fit[\'ci_low\']<=0<=fit[\'ci_high\']
  evidence[domain]=dict(bins=[q[0] for q in per],bin_mean_ns={str(q[0]):q[1] for q in per},
   plateau_ns=sum(q[1] for q in per)/len(per),slope_ns_per_bin=fit[\'slope\'],se_ns_per_bin=fit[\'se\'],
   ci_low_ns_per_bin=fit[\'ci_low\'],ci_high_ns_per_bin=fit[\'ci_high\'],dof=fit[\'dof\'],contains_zero=passed)
  if not passed:reasons.append(f\'domain{domain}:slope_ci_excludes_zero\')
 return dict(passed=not reasons,reasons=reasons,bins_used=use,domains=evidence)
'''
s = rep(s, "def peak(rows,per_node=False):", HELPER + "def peak(rows,per_node=False):")

# ---------------- 5(b): gate then activation ----------------
OLD = ("    if config['family']=='federation' and i==2:\n"
       "     import federation\n"
       "     bstart=time.monotonic_ns();probe_ids=federation.boundary(control,raw,MAPPED,"
       "config['method'],digest,health,lab/'shared/traces');"
       "timing['boundary_seconds']=(time.monotonic_ns()-bstart)/1e9")
NEW = ("    if config['family']=='federation' and i==2:\n"
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
s = rep(s, OLD, NEW)

P.write_text(s)
print("patched", P.name)