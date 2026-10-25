"""Reproduce every number in experiment_30of30/report.md.

Run from the repository root:
    python3 experiment_30of30/reproduce.py

It never writes to the official files (root output.csv, code/, log.txt). It regenerates the
baseline with the official code and the candidate with the experiment copy into a temporary
directory, compares their hashes with the stored files, runs the local checks on both, and
writes experiment_30of30/metrics/metrics.json and metrics.md.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, "experiment_30of30")
GOLD = os.path.join(ROOT, "dataset", "sample_output.csv")
BASE_CODE = os.path.join(ROOT, "code")
CAND_CODE = os.path.join(EXP, "code")
STORED = {"baseline": os.path.join(EXP, "baseline", "output_baseline.csv"),
          "candidate": os.path.join(EXP, "output_candidate.csv")}


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(*args):
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def ratio(text, name):
    m = re.search(rf"^{name}\s+(\d+)/(\d+)", text, re.M)
    return [int(m.group(1)), int(m.group(2))] if m else None


def scalar(text, name):
    m = re.search(rf"^{name}\s+([\d.]+)\s*$", text, re.M)
    return float(m.group(1)) if m else None


def rows(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    tmp = tempfile.mkdtemp(prefix="exp30_")
    out = {}
    for name, code in (("baseline", BASE_CODE), ("candidate", CAND_CODE)):
        eval_csv = os.path.join(tmp, f"{name}_eval.csv")
        eval_csv2 = os.path.join(tmp, f"{name}_eval_run2.csv")
        sample_csv = os.path.join(tmp, f"{name}_sample.csv")
        run(os.path.join(code, "main.py"), "--out", eval_csv)
        run(os.path.join(code, "main.py"), "--out", eval_csv2)
        run(os.path.join(code, "main.py"), "--sample", "--out", sample_csv)
        _, ev = run(os.path.join(code, "evaluation", "main.py"), "--pred", sample_csv, "--gold", GOLD)
        vrc, vt = run(os.path.join(code, "evaluation", "validate.py"), eval_csv)
        r = {
            "sha256": sha(eval_csv),
            "matches_stored_file": sha(eval_csv) == sha(STORED[name]),
            "deterministic_two_runs": sha(eval_csv) == sha(eval_csv2),
            "rows": len(rows(eval_csv)),
            "unique_case_ids": len({x["case_id"] for x in rows(eval_csv)}),
            "status_counts": {},
            "sample_status_accuracy": ratio(ev, "status_accuracy"),
            "sample_step_precision": ratio(ev, "step_precision"),
            "sample_step_recall": ratio(ev, "step_recall"),
            "sample_step_f1": scalar(ev, "step_f1"),
            "sample_field_accuracy_by_position": ratio(ev, "field_accuracy_by_position"),
            "sample_sequence_accuracy": ratio(ev, "sequence_accuracy"),
            "validator_exit_code": vrc,
            "validator_errors": int(re.search(r"(\d+) errors", vt).group(1)) if re.search(r"(\d+) errors", vt) else None,
        }
        for x in rows(eval_csv):
            r["status_counts"][x["deletion_status"]] = r["status_counts"].get(x["deletion_status"], 0) + 1
        steps = citations = 0
        for x in rows(eval_csv):
            for s in json.loads(x["steps_json"]):
                steps += 1
                citations += bool(s["evidence_id"])
        r["steps"], r["citations"] = steps, citations
        for script, key, pat in (("custom_cases.py", "custom_cases", r"(\d+)/(\d+) custom cases pass"),
                                 ("attribution_holdout.py", "attribution_holdout", r"accuracy: (\d+)/(\d+)"),
                                 ("history_crosscheck.py", "history_crosscheck", r"agreement (\d+)/(\d+)")):
            _, t = run(os.path.join(code, "evaluation", script))
            m = re.search(pat, t)
            r[key] = [int(m.group(1)), int(m.group(2))] if m else None
        out[name] = r
    _, diff = run(os.path.join(BASE_CODE, "evaluation", "compare.py"), "--old", STORED["baseline"],
                  "--new", STORED["candidate"])
    m = re.search(r"(\d+) of (\d+) cases changed", diff)
    out["diff"] = {"cases_changed": int(m.group(1)), "cases": int(m.group(2))} if m else None
    out["official_score"] = {"value": "28/30 as reported by the user",
                             "scored_file_sha256": "not confirmed: no score record or scored file in this workspace",
                             "local_confirmation_possible": False}
    os.makedirs(os.path.join(EXP, "metrics"), exist_ok=True)
    with open(os.path.join(EXP, "metrics", "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    def fmt(v):
        if isinstance(v, list):
            return f"{v[0]}/{v[1]}"
        if isinstance(v, dict):
            return ", ".join(f"{k} {n}" for k, n in sorted(v.items()))
        return str(v)

    keys = ["sha256", "matches_stored_file", "deterministic_two_runs", "rows", "unique_case_ids",
            "status_counts", "steps", "citations", "sample_status_accuracy", "sample_step_precision",
            "sample_step_recall", "sample_step_f1", "sample_field_accuracy_by_position",
            "sample_sequence_accuracy", "validator_errors", "custom_cases", "attribution_holdout",
            "history_crosscheck"]
    lines = ["| Metric | Baseline | Candidate |", "|---|---|---|"]
    for k in keys:
        lines.append(f"| {k} | {fmt(out['baseline'][k])} | {fmt(out['candidate'][k])} |")
    lines.append(f"| cases changed (baseline -> candidate) | - | {fmt(out['diff'])} |")
    lines.append("| official score | 28/30 (user-reported; file not confirmed) | not available locally |")
    with open(os.path.join(EXP, "metrics", "metrics.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
