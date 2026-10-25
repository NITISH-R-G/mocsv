# Candidate submission notes

| File | Purpose |
|---|---|
| `output_candidate.csv` | Candidate predictions for the 518 evaluation requests (not yet a release) |
| `evidence_ledger.json` | Per-case ledger: subject, each resource, how it was attributed (`linked` or `keyless:<kind>:<group>:<phase>:margin=`), category, policy action, and the state with its evidence id and source |
| `sample_predictions.csv` | Solver output for the 32 sample requests (scored in `code/evaluation/evaluation_report.md`) |

Regenerate with `python3 code/main.py --out submission/output_candidate.csv --ledger submission/evidence_ledger.json`.
The output is deterministic.

Validation: `python3 code/evaluation/validate.py submission/output_candidate.csv` reports 0 errors.

## Judgment calls (cases most likely to differ from the hidden answers)

* **case_63** — screenshot evidence_60 shows marketing profile marketing_001 for "Soo-jin Park"
  (sj.park@naver.com), while catalog activity_4008 links marketing_001 to kwame-mensah / user_09.
  It is treated as the subject's remaining MARKETING_PROFILE, so the case is INCOMPLETE with 3 steps.
  The alternative reading (someone else's profile) would leave no reportable record.
* **case_45** — only the catalog-featured `audit_record_003` (evidence_37) is verified. Five other
  security events on the same screen are context rows. The retention PDF evidence_55 is an
  alternative citation.
* **case_34** — `support_9538` is shown only as a reference on the profile page, and its PII scan
  predates the request. It is not reported.
* **Tombstone cases 543, 545, 547, 549** — the account absence cites the family file evidence_52.
  The only covering snapshot is evidence_02.

## Attribution ties (assignment margin < 0.5; about 50/50)

Accounts: case_59/60, 104/106, 203/205, 225/226, 243/244, 294/295, 345/346, 347/349,
404/406, 408/409, 411/412, 527/529. Analytics: case_155/157, 233/235.
Legal hold: case_287. Swapping a pair changes only those two cases' account or analytics step.

## Known unrecoverable gaps

92 cases have no security audit record in any participant file (sample analogues: case_18,
case_20). If the hidden answers contain one, it can't be derived without fabricating an id.
