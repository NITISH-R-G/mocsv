"""Holdout test for keyless attribution (workflow timing fingerprint).

Some accounts and analytics records are linked to their owner only by evidence captured
after the batch audit (late-exported logs, later snapshot bundles, operational history).
This test hides every identity key coming from those late files, re-runs the solver, and
checks that the records are re-attributed to the same case purely from deletion-worker
activity (window + reported-status match + sub-minute phase + evidence co-membership).

Usage:  python3 code/evaluation/attribution_holdout.py
"""
from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import solver  # noqa: E402

DATASET = os.path.join(os.path.dirname(os.path.dirname(HERE)), "dataset")
if not os.path.isdir(DATASET):  # experiment copy: use the repository dataset one level up
    DATASET = os.path.join(os.path.dirname(os.path.dirname(DATASET)), "dataset")


def main():
    full = solver.Solver(DATASET)
    reqs = full.load_requests(os.path.join(DATASET, "sample_deletion_requests.csv")) + \
        full.load_requests(os.path.join(DATASET, "deletion_requests.csv"))
    full.solve_all(reqs)
    # files captured after the audit of the batch whose records they describe
    late = set()
    for o in full.store.obs:
        if o["src"].startswith("history"):
            late.add(o["eid"])
    for c in full.cases.values():
        for rid in c["resources"]:
            for o in full.obs_by_rid.get(rid, []):
                if o["ts"] <= c["audit_at"] < o["cap"]:
                    late.add(o["eid"])
    truth = {cid: {r: h for r, h in c["resources"].items()
                   if h.startswith("linked") and r.startswith(("account_", "analytics_"))}
             for cid, c in full.cases.items()}

    hold = solver.Solver(DATASET)
    for o in hold.store.obs:
        if o["eid"] in late:
            o["keys"] = set()
    hold._link_resources()
    hold.solve_all(reqs)
    out = collections.Counter()
    for cid, rs in truth.items():
        for rid in rs:
            h2 = hold.cases[cid]["resources"].get(rid)
            kind = rid.split("_")[0]
            if h2 is None:
                owner = [x for x, c in hold.cases.items() if rid in c["resources"]]
                out[(kind, "wrong_or_missing")] += 1
                print(f"MISS {cid} {rid} -> {owner}")
            elif h2.startswith("keyless"):
                out[(kind, "re-attributed correctly")] += 1
            else:
                out[(kind, "still identity-linked")] += 1
    print("late files hidden:", sorted(late, key=lambda x: int(x.split("_")[1])))
    for k, v in sorted(out.items()):
        print(f"{k[0]:10s} {k[1]:26s} {v}")
    re_ok = sum(v for k, v in out.items() if k[1] == "re-attributed correctly")
    bad = sum(v for k, v in out.items() if k[1] == "wrong_or_missing")
    print(f"keyless re-attribution accuracy: {re_ok}/{re_ok + bad}")


if __name__ == "__main__":
    main()
