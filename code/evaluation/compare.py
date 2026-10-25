"""Diff two output CSVs (e.g. a baseline release and a new candidate).

Lists every case whose status or steps changed, with added/removed steps.

Usage:  python3 code/evaluation/compare.py --old baseline.csv --new submission/output_candidate.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    args = ap.parse_args()
    old, new = load(args.old), load(args.new)
    ids = sorted(set(old) | set(new), key=lambda x: int(x.split("_")[1]))
    changed = 0
    trans = Counter()
    for cid in ids:
        os_, osteps = old.get(cid, (None, []))
        ns, nsteps = new.get(cid, (None, []))
        trans[(os_, ns)] += 1
        o = Counter(tuple(s[f] for f in FIELDS) for s in osteps)
        n = Counter(tuple(s[f] for f in FIELDS) for s in nsteps)
        same_seq = [tuple(s[f] for f in FIELDS) for s in osteps] == [tuple(s[f] for f in FIELDS) for s in nsteps]
        if os_ == ns and same_seq:
            continue
        changed += 1
        print(f"== {cid}: {os_} -> {ns}")
        for s in sorted((o - n).elements()):
            print("   - ", s)
        for s in sorted((n - o).elements()):
            print("   + ", s)
        if o == n and not same_seq:
            print("   (same steps, different order)")
    print(f"\n{changed} of {len(ids)} cases changed")
    for (a, b), v in sorted(trans.items(), key=lambda x: str(x)):
        print(f"   {a} -> {b}: {v}")


if __name__ == "__main__":
    main()
