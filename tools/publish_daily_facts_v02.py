#!/usr/bin/env python3
"""Certify one CNEquity date offline; --execute appends to Facts authority."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_data.daily_facts_v02_publish import publish

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date', required=True)
    p.add_argument('--data-root', type=Path, default=Path('/Users/luke808/AI/local-a-share-data-service-data'))
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--replace-manifest-hash', help='Exact current Facts hash for an immutable same-date V02 repair')
    args = p.parse_args()
    result = publish(args.data_root, day=args.date, stage=args.stage, evidence=args.evidence, execute=args.execute, replace_manifest_hash=args.replace_manifest_hash)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    sys.exit(0 if result['status'] in {'READY', 'PUBLISHED'} else 2)
