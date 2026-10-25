"""Additional evaluation cases created for this solution.

Each custom case is a synthetic request row built from participant-visible data, with an
expected answer derived by hand from the evidence (documented below and in
evaluation_report.md). A custom case replaces the request it is derived from (if any) and
is solved together with every other request, so shared deletion-worker windows still
interact exactly as in the real run.

Usage:  python3 code/evaluation/custom_cases.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
sys.path.insert(0, CODE)

import solver  # noqa: E402

DATASET = os.path.join(os.path.dirname(CODE), "dataset")


def step(n, phase, system, rid, op, ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid,
            "operation": op, "state": solver.STATE_OF[op], "evidence_id": ev}


CUSTOM = [
    {
        "id": "custom_01_german_deletion",
        "replaces": "case_21",
        "why": "Same subject/dates as sample case_21 but the request is written in German. "
               "Deletion intent must be recognised in a language absent from the dataset.",
        "request": {"case_id": "case_21", "email": "amara-okeke@proton.me",
                    "requested_at": "2026-09-04T12:00:00Z", "reported_status": "COMPLETE",
                    "reported_at": "2026-09-05T12:00:00Z", "audit_at": "2026-09-15T12:00:00Z",
                    "request_text": "Ich habe mein Konto löschen lassen. Bitte prüfen Sie, ob das Profil "
                                    "wirklich gelöscht wurde und nur die gesetzlich nötigen Sicherheitsdaten bleiben."},
        "expected_status": "COMPLETE",
        "expected": [step(1, "VERIFICATION", "account_service", "account_6440", "VERIFY_RECORD_ABSENT", "evidence_92"),
                     step(2, "VERIFICATION", "security_service", "audit_record_3737", "VERIFY_RECORD_PRESENT", "evidence_92"),
                     step(3, "VERIFICATION", "analytics_platform", "analytics_5335", "VERIFY_RECORD_ANONYMIZED", "evidence_92")],
    },
    {
        "id": "custom_02_reaudit_after_recreation",
        "replaces": "case_27",
        "why": "Sample case_27 re-audited at 2026-09-17T13:00Z. The snapshot bundle evidence_02 "
               "(captured 09-17T12:00Z) is now admissible and shows account_3866 ACTIVE again with "
               "full PII at 09-17T12:00Z, so the deletion is no longer complete. Tests that the "
               "audit_at cutoff, not the original answer, decides the state.",
        "request": {"case_id": "case_27", "email": "ananya-nair@gmail.com",
                    "requested_at": "2026-08-29T12:00:00Z", "reported_status": "COMPLETE",
                    "reported_at": "2026-08-30T12:00:00Z", "audit_at": "2026-09-17T13:00:00Z",
                    "request_text": "Please audit the deletion again now and confirm that nothing identifies me."},
        "expected_status": "INCOMPLETE",
        "expected": [step(1, "FINDING", "account_service", "account_3866", "REPORT_REMAINING_RECORD", "evidence_02"),
                     step(2, "REMEDIATION", "account_service", "account_3866", "DELETE_RECORD"),
                     step(3, "VERIFICATION", "account_service", "account_3866", "VERIFY_RECORD_ABSENT"),
                     step(4, "VERIFICATION", "security_service", "audit_record_3830", "VERIFY_RECORD_PRESENT"),
                     step(5, "VERIFICATION", "support_platform", "support_4707", "VERIFY_RECORD_REDACTED"),
                     step(6, "VERIFICATION", "analytics_platform", "analytics_4574", "VERIFY_RECORD_ANONYMIZED"),
                     step(7, "VERIFICATION", "integration_service", "data_flow_2295", "VERIFY_DATA_FLOW_DISABLED"),
                     step(8, "VERIFICATION", "backup_service", "backup_5970", "VERIFY_RESTORE_BLOCKED")],
    },
    {
        "id": "custom_03_spanish_access_request",
        "replaces": None,
        "why": "Spanish access/portability request from the subject of sample case_23 that explicitly "
               "says not to delete anything: must be NOT_APPLICABLE with [] even though the subject "
               "has remaining records.",
        "request": {"case_id": "case_9001", "email": "thandiwe.m6866@zoho.com",
                    "requested_at": "2026-09-10T12:00:00Z", "reported_status": "NOT_STARTED",
                    "reported_at": "2026-09-11T12:00:00Z", "audit_at": "2026-09-15T12:00:00Z",
                    "request_text": "Quisiera recibir una copia de mis datos personales. Por favor, no eliminen "
                                    "mi cuenta ni cambien nada."},
        "expected_status": "NOT_APPLICABLE",
        "expected": [],
    },
    {
        "id": "custom_04_current_email_for_renamed_user",
        "replaces": "case_29",
        "why": "Sample case_29 submitted with the subject's CURRENT email (noah.f4380@outlook.com) "
               "instead of the historical one. Identity resolution must reach the same records, "
               "which are indexed under the historical address.",
        "request": {"case_id": "case_29", "email": "noah.f4380@outlook.com",
                    "requested_at": "2026-08-27T12:00:00Z", "reported_status": "NOT_STARTED",
                    "reported_at": "2026-08-30T12:00:00Z", "audit_at": "2026-09-15T12:00:00Z",
                    "request_text": "Please delete my account and every record under my old and new email."},
        "expected_status": "INCOMPLETE",
        "expected": None,  # filled from sample_output.csv case_29 at runtime
    },
]


def main():
    import csv
    with open(os.path.join(DATASET, "sample_output.csv"), encoding="utf-8") as fh:
        gold = {r["case_id"]: json.loads(r["steps_json"]) for r in csv.DictReader(fh)}
    s = solver.Solver(DATASET)
    base = s.load_requests(os.path.join(DATASET, "sample_deletion_requests.csv")) + \
        s.load_requests(os.path.join(DATASET, "deletion_requests.csv"))
    passed = 0
    for cc in CUSTOM:
        reqs = [r for r in base if r["case_id"] != cc["replaces"]] + [cc["request"]]
        res = s.solve_all(reqs)[cc["request"]["case_id"]]
        expected = cc["expected"] if cc["expected"] is not None else gold["case_29"]
        ok_status = res["status"] == cc["expected_status"]
        ok_steps = [tuple(sorted(x.items())) for x in res["steps"]] == [tuple(sorted(x.items())) for x in expected]
        passed += ok_status and ok_steps
        print(f"{cc['id']:42s} status {'OK ' if ok_status else 'BAD'} ({res['status']})  steps {'OK' if ok_steps else 'DIFF'} "
              f"({len(res['steps'])}/{len(expected)})")
        if not ok_steps:
            for x in res["steps"]:
                print("   got     ", x)
            for x in expected:
                print("   expected", x)
    print(f"{passed}/{len(CUSTOM)} custom cases pass")


if __name__ == "__main__":
    main()
