from __future__ import annotations
import argparse
import json

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument('--workspace', required=True); parser.add_argument('--arm', required=True); parser.add_argument('--evidence', required=True)
    args = parser.parse_args(); evidence = json.loads(open(args.evidence, encoding='utf-8').read())
    cases = evidence.get('cases'); aggregate = evidence.get('aggregate'); gate = evidence.get('eligibility_gate'); records = evidence.get('records')
    derived = {'case_count': len(cases) if isinstance(cases, list) else -1, 'passed_count': sum(1 for c in cases if isinstance(c, dict) and c.get('outcome') is True) if isinstance(cases, list) else -1, 'failed_count': sum(1 for c in cases if isinstance(c, dict) and c.get('outcome') is False) if isinstance(cases, list) else -1}
    if isinstance(gate, dict):
        eligible = bool(gate.get('all_pairs_measured') is True and gate.get('success_difference_pp', -1) >= 10.0 and gate.get('success_lower95_pp', -1) > 0.0 and gate.get('token_or_wall_upper95_ratio', 1.0) < 0.85 and gate.get('out_of_scope_edits', 1) == 0 and gate.get('regressions', 1) == 0)
    else: eligible = False
    checks = {'case_schema': isinstance(cases, list) and all(isinstance(c, dict) and set(c) >= {'case_id', 'expected', 'actual', 'outcome'} and c['outcome'] is (c['actual'] == c['expected']) for c in cases), 'aggregate_derived': isinstance(aggregate, dict) and all(aggregate.get(k) == v for k, v in derived.items()), 'records_present': isinstance(records, list) and bool(records), 'eligibility_derived': isinstance(gate, dict) and gate.get('claim_eligible') is eligible}
    print(json.dumps({'calibration_id': 'c06-report-generation', 'checks': checks, 'derived_claim_eligible': eligible, 'status': 'PASS' if all(checks.values()) else 'FAIL'}, sort_keys=True))

if __name__ == '__main__': main()
