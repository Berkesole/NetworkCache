#!/usr/bin/env python3
"""Version an execution-only repair without rewriting any raw provenance."""
import hashlib
import json
import shutil
from pathlib import Path
import spec

HERE = Path(__file__).resolve().parent
PARENT = '801a58c3620133850a84b3f88237dbb9375642800b72b52966bc0ca395fb2f80'


def main():
    old = json.loads((HERE / 'frozen_campaign.json').read_text())
    if old['campaign_fingerprint'] != PARENT:
        raise SystemExit('Expected original formal campaign; refuse repeated revision')
    archive = HERE / 'execution-revisions' / PARENT
    archive.mkdir(parents=True, exist_ok=False)
    for name in ['frozen_campaign.json', 'assignment-global.json',
                 'assignment-domain-a.json', 'assignment-domain-b.json']:
        shutil.copy2(HERE / name, archive / name)
    campaign = dict(old)
    campaign.pop('campaign_fingerprint')
    campaign['execution_revision'] = {
        'id': 'tcp-logical-activation-env-v3',
        'parent_campaign': PARENT,
        'reason': 'Pass logical-only activation mode explicitly inside Kathara exec TCP helper',
        'compatible_prior_nc_campaigns': [PARENT],
        'prior_attempts': 'NC remains compatible; failed TCP attempts001-004 retained and unselected',
    }
    campaign['resume_attempt_floors'] = {}
    campaign['execution_attempt_floors'] = {'tcp-direct-seed001-rep1': 5}
    campaign['execution_attempt_budget'] = '4 attempts per slot for repaired execution; first TCP uses005-008; old failed001-004 retained'
    campaign['results_root'] = '/data/networkcache-cross-host-federation-v1/results-pre-isolation-v2'
    campaign['pre_isolation_policy'] = '32 explicit unreachable remote /32 routes per border before helpers; replace only at activation'
    campaign['orchestrator_source_sha256'] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob('*.py'))
    }
    campaign['campaign_fingerprint'] = spec.sha256_bytes(spec.canonical_json(campaign))
    assignment = json.loads((HERE / 'assignment-global.json').read_text())
    assignment.pop('assignment_fingerprint')
    assignment['campaign_fingerprint'] = campaign['campaign_fingerprint']
    assignment['assignment_fingerprint'] = spec.sha256_bytes(spec.canonical_json(assignment))
    (HERE / 'frozen_campaign.json').write_bytes(spec.canonical_json(campaign))
    (HERE / 'assignment-global.json').write_bytes(spec.canonical_json(assignment))
    for role in ['domain-a', 'domain-b']:
        shard = json.loads((HERE / f'assignment-{role}.json').read_text())
        shard.pop('shard_fingerprint')
        shard['campaign_fingerprint'] = campaign['campaign_fingerprint']
        shard['assignment_fingerprint'] = assignment['assignment_fingerprint']
        shard['shard_fingerprint'] = spec.sha256_bytes(spec.canonical_json(shard))
        (HERE / f'assignment-{role}.json').write_bytes(spec.canonical_json(shard))
    print(campaign['campaign_fingerprint'], assignment['assignment_fingerprint'])


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--execute']:
        raise SystemExit('requires --execute')
    main()
