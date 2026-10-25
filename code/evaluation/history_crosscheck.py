"""Cross-check against operational-history summaries.

Three later snapshot bundles contain an `operational_record_history` collection that lists,
for one earlier audit batch, every record with its record_type and result at the batch's
audit time. Those files were captured after that audit, so they are never cited; here they
serve as an independent check of discovery and state reconstruction for the 30 cases of
those batches.

Usage:  python3 code/evaluation/history_crosscheck.py
"""
from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import solver  # noqa: E402

DATASET = os.path.join(os.path.dirname(os.path.dirname(HERE)), "dataset")
FINDING_RESULTS = {"FOUND", "IDENTIFIABLE", "ACTIVE"}


def main():
    s = solver.Solver(DATASET)
    reqs = s.load_requests(os.path.join(DATASET, "sample_deletion_requests.csv")) + \
        s.load_requests(os.path.join(DATASET, "deletion_requests.csv"))
    s.solve_all(reqs)
    hist = [o for o in s.store.obs if o["src"].startswith("history")]
    by_time = collections.defaultdict(list)
    for o in hist:
        by_time[o["ts"]].append(o)
    owner = collections.defaultdict(set)
    for c in s.cases.values():
        for r in c["resources"]:
            owner[r].add(c["case_id"])
    tot_ok = tot = 0
    for t, obs in sorted(by_time.items()):
        batch = [c for c in s.cases.values() if c["audit_at"] == t]
        if not batch:
            continue
        mine = {r for c in batch for r in c["resources"]}
        theirs = {o["rid"] for o in obs}
        agree = 0
        for o in obs:
            if o["rid"] not in mine:
                continue
            c = next(s.cases[x] for x in owner[o["rid"]] if s.cases[x]["audit_at"] == t)
            mine_finding = s._finding_for(o["rid"], s.state(o["rid"], c), c)
            hist_finding = o["result"] in FINDING_RESULTS and o["system"] != "security_service"
            agree += mine_finding == hist_finding
        tot_ok += agree
        tot += len(obs)
        print(f"batch audit {t}: cases={len(batch)} history_records={len(theirs)} solver_records={len(mine)} "
              f"missing={sorted(theirs - mine)} extra={sorted(mine - theirs)} finding_state_agreement={agree}/{len(obs)}")
    print(f"overall finding-state agreement {tot_ok}/{tot}")


if __name__ == "__main__":
    main()
