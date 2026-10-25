"""Local evaluator: compare a predictions CSV with a reference CSV (e.g. sample_output.csv).

Metrics (all reported with numerator/denominator):
  * status accuracy          - deletion_status exact match per case
  * step precision / recall  - multiset match of full step tuples
                               (phase, system_id, resource_id, operation, state, evidence_id)
  * resource-step P/R        - same but ignoring evidence_id (discovery/policy quality)
  * field accuracy           - per aligned position (step_no), each of the 6 fields
  * sequence accuracy        - entire steps array identical (order included)
  * evidence accuracy        - for steps matched without evidence, evidence_id equal

Usage:
  python3 code/evaluation/main.py --pred output.csv --gold dataset/sample_output.csv
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


def tup(s, with_ev=True):
    return tuple(s[f] for f in (FIELDS if with_ev else FIELDS[:-1]))


def evaluate(pred, gold, case_ids=None, verbose=False):
    ids = sorted(case_ids or gold, key=lambda x: int(x.split("_")[1]))
    m = Counter()
    per_case = {}
    for cid in ids:
        gs, gsteps = gold[cid]
        ps, psteps = pred.get(cid, (None, []))
        m["cases"] += 1
        m["status_ok"] += ps == gs
        g = Counter(tup(s) for s in gsteps)
        p = Counter(tup(s) for s in psteps)
        tp = sum((g & p).values())
        m["tp"] += tp
        m["gold_steps"] += sum(g.values())
        m["pred_steps"] += sum(p.values())
        g2 = Counter(tup(s, False) for s in gsteps)
        p2 = Counter(tup(s, False) for s in psteps)
        m["tp_noev"] += sum((g2 & p2).values())
        for i in range(max(len(gsteps), len(psteps))):
            for f in FIELDS:
                m["field_total"] += 1
                if i < len(gsteps) and i < len(psteps) and gsteps[i][f] == psteps[i][f]:
                    m["field_ok"] += 1
        seq = [tup(s) for s in gsteps] == [tup(s) for s in psteps]
        m["seq_ok"] += seq
        per_case[cid] = {"status": (ps, gs), "tp": tp, "gold": sum(g.values()), "pred": sum(p.values()),
                         "seq": seq, "missing": sorted((g - p).elements()), "extra": sorted((p - g).elements())}
    def ratio(a, b):
        return f"{m[a]}/{m[b]} = {m[a] / m[b]:.4f}" if m[b] else f"{m[a]}/0"
    prec = m["tp"] / m["pred_steps"] if m["pred_steps"] else 0
    rec = m["tp"] / m["gold_steps"] if m["gold_steps"] else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    prec2 = m["tp_noev"] / m["pred_steps"] if m["pred_steps"] else 0
    rec2 = m["tp_noev"] / m["gold_steps"] if m["gold_steps"] else 0
    report = {
        "status_accuracy": ratio("status_ok", "cases"),
        "step_precision": ratio("tp", "pred_steps"),
        "step_recall": ratio("tp", "gold_steps"),
        "step_f1": f"{f1:.4f}",
        "step_precision_ignoring_evidence": ratio("tp_noev", "pred_steps"),
        "step_recall_ignoring_evidence": ratio("tp_noev", "gold_steps"),
        "step_f1_ignoring_evidence": f"{(2 * prec2 * rec2 / (prec2 + rec2)) if prec2 + rec2 else 0:.4f}",
        "field_accuracy_by_position": ratio("field_ok", "field_total"),
        "sequence_accuracy": ratio("seq_ok", "cases"),
    }
    return report, per_case


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    gold = load(args.gold)
    pred = load(args.pred)
    report, per_case = evaluate(pred, gold)
    for k, v in report.items():
        print(f"{k:38s} {v}")
    if args.verbose:
        for cid, r in per_case.items():
            if r["status"][0] != r["status"][1] or not r["seq"]:
                print(f"\n== {cid} status pred={r['status'][0]} gold={r['status'][1]} tp={r['tp']}/{r['gold']} pred={r['pred']}")
                for s in r["missing"]:
                    print("   MISSING", s)
                for s in r["extra"]:
                    print("   EXTRA  ", s)


if __name__ == "__main__":
    main()
