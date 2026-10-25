"""Ghost Data Still Exists - deletion audit agent.

Usage (from the repository root):
    python3 code/main.py                      # writes output.csv for dataset/deletion_requests.csv
    python3 code/main.py --sample             # solves the 32 sample requests -> sample_predictions.csv
    python3 code/main.py --out path.csv       # custom output path
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import solver  # noqa: E402

ROOT = os.path.dirname(HERE)
DATASET = os.path.join(ROOT, "dataset")
if not os.path.isdir(DATASET):  # experiment copy: use the repository dataset one level up
    DATASET = os.path.join(os.path.dirname(os.path.dirname(DATASET)), "dataset")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="solve sample_deletion_requests.csv")
    ap.add_argument("--out", default=None)
    ap.add_argument("--ledger", default=None, help="optional JSON path for the per-case evidence ledger")
    args = ap.parse_args()
    s = solver.Solver(DATASET)
    # all requests are solved together so that shared activity windows are reasoned jointly
    sample = s.load_requests(os.path.join(DATASET, "sample_deletion_requests.csv"))
    evalr = s.load_requests(os.path.join(DATASET, "deletion_requests.csv"))
    results = s.solve_all(sample + evalr)
    wanted = {r["case_id"] for r in (sample if args.sample else evalr)}
    chosen = {k: v for k, v in results.items() if k in wanted}
    out = args.out or os.path.join(ROOT, "sample_predictions.csv" if args.sample else "output.csv")
    solver.write_output(chosen, out)
    if args.ledger:
        with open(args.ledger, "w", encoding="utf-8") as fh:
            json.dump({k: v["ledger"] for k, v in chosen.items()}, fh, indent=1, default=list)
    print(f"wrote {len(chosen)} rows to {out}")


if __name__ == "__main__":
    main()
