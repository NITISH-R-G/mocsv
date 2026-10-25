"""Contract validator for an output CSV (schema, ordering, citations, admissibility).

Usage:
  python3 code/evaluation/validate.py output.csv                       # vs dataset/deletion_requests.csv
  python3 code/evaluation/validate.py preds.csv --requests dataset/sample_deletion_requests.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET = os.path.join(ROOT, "dataset")
if not os.path.isdir(DATASET):  # experiment copy: use the repository dataset one level up
    DATASET = os.path.join(os.path.dirname(os.path.dirname(DATASET)), "dataset")

OPS = {
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate(path, requests_path):
    errors = []
    with open(requests_path, encoding="utf-8") as fh:
        reqs = {r["case_id"]: r for r in csv.DictReader(fh)}
    with open(os.path.join(DATASET, "evidence_files.csv"), encoding="utf-8") as fh:
        files = {r["file_id"]: r for r in csv.DictReader(fh)}
    with open(os.path.join(DATASET, "system_activity.csv"), encoding="utf-8") as fh:
        acts = {r["activity_id"]: r for r in csv.DictReader(fh)}
    with open(os.path.join(DATASET, "user_audit_log.csv"), encoding="utf-8") as fh:
        audits = {r["audit_id"]: r for r in csv.DictReader(fh)}

    def cited_time(eid):
        if eid in files:
            return files[eid]["captured_at"]
        if eid in acts:
            return acts[eid]["event_time"]
        if eid in audits:
            return audits[eid]["event_time"]
        return None

    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errors.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if len(rows) != len(reqs):
        errors.append(f"row count {len(rows)} != {len(reqs)}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case ids")
    if set(ids) != set(reqs):
        errors.append(f"case id mismatch missing={sorted(set(reqs) - set(ids))[:5]} extra={sorted(set(ids) - set(reqs))[:5]}")
    if ids != sorted(ids, key=lambda x: int(x.split("_")[1])):
        errors.append("rows not sorted by case_id")
    stats = {"COMPLETE": 0, "INCOMPLETE": 0, "NOT_APPLICABLE": 0, "steps": 0, "citations": 0}
    for row in rows:
        if len(row) != 3:
            errors.append(f"{row[:1]} wrong column count")
            continue
        cid, status, raw = row
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errors.append(f"{cid} bad status {status}")
            continue
        stats[status] += 1
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append(f"{cid} invalid JSON {e}")
            continue
        if not isinstance(steps, list):
            errors.append(f"{cid} steps_json not an array")
            continue
        stats["steps"] += len(steps)
        audit = reqs.get(cid, {}).get("audit_at", "9999")
        if status == "NOT_APPLICABLE":
            if steps:
                errors.append(f"{cid} NOT_APPLICABLE must have []")
            continue
        if not steps:
            errors.append(f"{cid} {status} with no steps")
        phases_seen = []
        last = {}
        for i, s in enumerate(steps, 1):
            if set(s) != KEYS:
                errors.append(f"{cid} step {i} keys {sorted(s)}")
                continue
            if s["step_no"] != i:
                errors.append(f"{cid} step_no {s['step_no']} != {i}")
            ph, op = s["phase"], s["operation"]
            if ph not in OPS or op not in OPS[ph]:
                errors.append(f"{cid} step {i} invalid phase/op {ph}/{op}")
                continue
            if OPS[ph][op] != s["state"]:
                errors.append(f"{cid} step {i} state {s['state']} != {OPS[ph][op]}")
            phases_seen.append(ph)
            key = (list(OPS[ph]).index(op), s["system_id"], s["resource_id"])
            if ph in last and key < last[ph]:
                errors.append(f"{cid} step {i} out of order within {ph}")
            last[ph] = key
            ev = s["evidence_id"]
            needs = ph == "FINDING" or (status == "COMPLETE" and ph == "VERIFICATION")
            if needs:
                if not ev:
                    errors.append(f"{cid} step {i} missing evidence")
                else:
                    stats["citations"] += 1
                    t = cited_time(ev)
                    if t is None:
                        errors.append(f"{cid} step {i} unknown evidence {ev}")
                    elif t > audit:
                        errors.append(f"{cid} step {i} cites {ev} captured {t} after audit_at {audit}")
            elif ev:
                errors.append(f"{cid} step {i} future step must have empty evidence")
        if phases_seen != sorted(phases_seen, key=PHASES.index):
            errors.append(f"{cid} phases out of order")
        if status == "COMPLETE" and set(phases_seen) - {"VERIFICATION"}:
            errors.append(f"{cid} COMPLETE with non-verification steps")
        if status == "INCOMPLETE" and "FINDING" not in phases_seen:
            errors.append(f"{cid} INCOMPLETE without findings")
        dup = len(steps) != len({(s["phase"], s["resource_id"], s["operation"]) for s in steps})
        if dup:
            errors.append(f"{cid} duplicate steps")
    return errors, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--requests", default=os.path.join(DATASET, "deletion_requests.csv"))
    args = ap.parse_args()
    errors, stats = validate(args.path, args.requests)
    print(json.dumps(stats))
    for e in errors[:200]:
        print("ERROR", e)
    print(f"{len(errors)} errors")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
