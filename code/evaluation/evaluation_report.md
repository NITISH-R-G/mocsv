# Evaluation report — Ghost Data Still Exists deletion-audit agent

All numbers below come from scripts in `code/evaluation/` run on the final code. Every
metric is shown as numerator/denominator. **No official score has been computed by me:** the
hidden grader isn't available, so nothing here is an official HackerRank result.

| Script | What it measures |
|---|---|
| `main.py --pred P --gold G` | status accuracy, step precision/recall/F1, field accuracy, sequence accuracy |
| `validate.py OUT` | contract checks (schema, ordering, citations, citation admissibility) |
| `custom_cases.py` | additional evaluation cases created for this solution |
| `attribution_holdout.py` | holdout test of keyless resource attribution |
| `history_crosscheck.py` | independent check of discovery + state reconstruction |
| `compare.py --old A --new B` | full diff between a baseline release and a candidate |

## 1. Sample set (32 public requests, `dataset/sample_output.csv`)

The sample answers were used to discover the general rules, so these are **in-sample**
numbers. Independent checks follow in sections 3–5.

| Metric | Result |
|---|---|
| Case-level `deletion_status` accuracy | 32/32 = 1.0000 |
| Step precision (all 6 fields incl. `evidence_id`) | 190/190 = 1.0000 |
| Step recall (all 6 fields) | 190/193 = 0.9845 |
| Step F1 | 0.9922 |
| Step precision / recall ignoring `evidence_id` | 190/190, 190/193 |
| Field accuracy (aligned by step position, 6 fields) | 1119/1158 = 0.9663 |
| Sequence accuracy (whole `steps_json` array identical) | 29/32 = 0.9062 |

The three missed golden steps can't be derived from participant data:

* `case_04` `account_004` VERIFY_RECORD_ABSENT: the id appears in no evidence file and no activity row.
* `case_18` `audit_record_6645` and `case_20` `audit_record_5569` VERIFY_RECORD_PRESENT: these ids appear nowhere in participant data.

The 29 cases without a missed step are exact matches. The solver's sample-set ceiling is
therefore 190/193.

## 2. Full evaluation run (518 requests)

* `validate.py submission/output_candidate.csv` → **0 errors**. It checks:
  * header and 518 unique rows sorted by `case_id`, with every request present;
  * JSON arrays with consecutive `step_no`;
  * valid phase/operation/state triples, with phase order and in-phase order (operation table order, then `system_id`, then `resource_id`);
  * citations: every finding and every COMPLETE verification cites a `file_id`/`activity_id`, and every cited file was captured at or before the case's `audit_at`;
  * remediation and future verification steps carry empty evidence; `NOT_APPLICABLE` rows are `[]`.
* Status distribution: COMPLETE 153, INCOMPLETE 355, NOT_APPLICABLE 10; 3,254 steps; 1,246 citations.
* Runs are deterministic: two runs produced byte-identical files. A full run takes about 3 s with no model calls.

## 3. Additional evaluation cases created for this solution (`custom_cases.py`)

Each custom request replaces the request it is derived from and is solved together with all
other requests. That way shared deletion-worker windows still compete, as in the real run.

| Case | What it tests | Expected (derived by hand) | Result |
|---|---|---|---|
| custom_01_german_deletion | Sample case_21 rewritten in German ("Ich habe mein Konto löschen lassen …") | COMPLETE, 3 verification steps citing evidence_92 | **pass** (failed before the multilingual fix, see §6) |
| custom_02_reaudit_after_recreation | Sample case_27 re-audited at 2026-09-17T13:00Z: evidence_02 (captured 09-17T12:00Z) becomes admissible and shows account_3866 ACTIVE again | INCOMPLETE: REPORT_REMAINING_RECORD account_3866 (evidence_02), DELETE, and 6 verification steps | **pass** |
| custom_03_spanish_access_request | Spanish access request "no eliminen mi cuenta" from a subject with remaining records | NOT_APPLICABLE `[]` | **pass** |
| custom_04_current_email_for_renamed_user | Sample case_29 submitted with the current email; the records are indexed under the historical email | Same 14 steps as golden case_29 | **pass** |

Final: 4/4 pass.

## 4. Holdout test of keyless attribution (`attribution_holdout.py`)

About 280 deleted accounts and 117 anonymized analytics records carry no identifier: they
appear only as deletion-worker activity, or as identifier-free rows. To test the attribution
method, all identity keys from evidence captured after its batch audit were hidden. That
covers 17 late files: late-exported logs, later snapshot bundles, and operational history.
The solver was then re-run.

| Records whose only identity link was hidden | Re-attributed to the same case from activity alone |
|---|---|
| accounts | 79/79 |
| analytics | 1/1 |
| **total** | **80/80** |

## 5. Independent state check (`history_crosscheck.py`)

Three later snapshot bundles contain an `operational_record_history` that lists every
record of one earlier audit batch with its `record_type` and result. The batches are
02-18, 05-04 and 06-23, 30 cases in total. Those files postdate the batches' `audit_at`, so
they are never cited; they are used only to check the solver.

| Batch audit_at | History records | Solver records | Missing | Extra | Finding/no-finding agreement |
|---|---|---|---|---|---|
| 2026-02-18T09:00Z | 40 | 40 | 0 | 0 | 40/40 |
| 2026-05-04T10:00Z | 37 | 37 | 0 | 0 | 37/37 |
| 2026-06-23T06:00Z | 40 | 40 | 0 | 0 | 40/40 |

Also checked: for every batch that has a late-captured file, the state that file records at
`audit_at` agrees with the solver's admissible state. The only differences were stale
pre-batch "present" snapshots, where the newer admissible absence correctly wins.

## 6. Failures found and improvements made

| Iteration | Change | Samples: status / step F1 / sequence |
|---|---|---|
| v1 | Naive: identity-linked resources, every activity in a ±window, snapshot omission = absence for all systems | 28/32 / 0.829 / 9/32 |
| v2 | Latest admissible explicit observation decides state; joint keyless assignment; deletion intent = "authorizes unless explicitly declined" (follow-up "verify my deletion" requests were wrongly NOT_APPLICABLE) | 32/32 / 0.941 / 16/32 |
| v3 | Snapshot omission proves absence only for account tables: bundle and inventory extracts are partial, and treating them as complete dropped retained audit records. Uncitable but timestamped-before-audit evidence is used for discovery only. Worker operation type (DELETE/REDACT) decides support message vs attachment. Accounts the evidence shows absent are scored towards COMPLETE cases | 32/32 / 0.984 / 27/32 |
| v4 | Per-template activity slots (policy actions at −29/−22/−15/−8 min, final results at +38…+133 min). Workflow timing fingerprint: every deletion-worker run shares one sub-minute phase (spread ≤2 s, measured on identity-linked cases) | 32/32 / **0.992** / **29/32** |
| v5 | Continuous phase distance instead of a hard threshold (2 account swaps in the eval set); assignment margins written to the ledger | unchanged on samples |
| v6 | Screenshot context rows don't open a resource on their own (only the featured or catalog-linked record does). A COMPLETE case whose account is proven absent only by omission cites the case's own evidence file. Multilingual intent (de/it/nl/pl/ru/zh/ar and negations), found by custom_01 | unchanged on samples; custom cases 4/4 |

Other failure modes that were found, with their fixes:

* Episode scoping: three users have two cases at different dates. Records from the other
  episode (e.g. `object_19` in February for case_07 in September) are excluded. A resource
  needs evidence or worker activity inside `[requested_at, audit_at]`.
* Temporal traps: re-created records after `audit_at` (bundle snapshots stamped 1–2 days
  later, logs exported days later) are ignored. Stale "present" reports from before the
  batch are overridden by the newer admissible state.
* Replication drift: an account present only in a read, regional, reporting or failover
  replica table is still a remaining record.
* Decoys: near-miss emails (`g.silva76.jr@…`, `smartinez6461.jr@…`, `camila.almeida.jr@…`)
  and staff or other-account rows are never linked. Linking is exact on email, phone, email
  hash and user id; names are never used.

## 7. Known limitations (where the remaining error is expected)

* **Audit records absent from all participant data:** 92 cases, mostly INCOMPLETE batches
  whose evidence omits them. Golden answers probably contain a VERIFY_RECORD_PRESENT step
  that no evidence supports. Sample analogues: case_18 and case_20.
* **Irreducible attribution ties:** about 24 accounts, 4 analytics records and 1 legal hold
  have an assignment margin below 0.5. Either two runs share the same sub-minute phase, or
  neither candidate case has any other worker activity. The listing is in the submission
  notes and in the ledger (`margin=` in the `how` field).
* **Artifact cases (judgment calls):**
  * case_63: the marketing console shows "Soo-jin Park". The data-catalog link says
    marketing_001 belongs to kwame-mensah. Treated as the subject's record (INCOMPLETE).
  * case_45: only the catalog-featured `audit_record_003` is verified. The other five
    security events listed on the same screen are context rows.
  * case_34: `support_9538` (a context reference on the profile page, last scanned before
    the request) is not reported.
* **Tombstone family 543–549:** COMPLETE accounts cite the family file evidence_52, which
  has no account table. The only covering snapshot is evidence_02.
