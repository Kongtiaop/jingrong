from __future__ import annotations
import argparse, asyncio, json, subprocess, sys

COMMANDS = [
 ("uow_shape", [sys.executable, "-m", "scripts.test_uow_contract_shape"]),
 ("repository_contracts", [sys.executable, "-m", "scripts.test_repository_contracts"]),
 ("dual_matrix", [sys.executable, "-m", "scripts.test_dual_contract_matrix"]),
 ("error_mapping", [sys.executable, "-m", "scripts.test_database_error_mapping_contract"]),
 ("semantic_contract", [sys.executable, "-m", "scripts.test_dual_contract_semantics"]),
 ("transaction_error", [sys.executable, "-m", "scripts.test_dual_transaction_error_contract"]),
]
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-repository', action='store_true')
    args = ap.parse_args()
    results = []
    for name, cmd in COMMANDS:
        if args.skip_repository and name == 'repository_contracts':
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        output = (proc.stdout or '') + (proc.stderr or '')
        results.append({'name': name, 'returncode': proc.returncode, 'output': output[-2000:], 'pass': proc.returncode == 0})
    result = {'gate': 'dual_contract_gate', 'performance_tests': False, 'results': results, 'pass': all(x['pass'] for x in results)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['pass']:
        raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
