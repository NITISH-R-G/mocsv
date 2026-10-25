# Experiment 30/30: recovering the missing 2 points

**Result: 30/30 is not confirmed and cannot be confirmed locally.** No official evaluator, scored file
or score breakdown exists in this workspace. Every justified change I could find leaves the CSV
byte-identical to the baseline, so the candidate equals the baseline.

| Deliverable | Path | SHA-256 | Bytes |
|---|---|---|---|
| Candidate CSV | `experiment_30of30/output_candidate.csv` | `b88c0b302143be710e201278d270a534a495c93b30068f23e188ebf19897bd64` | 650,149 |
| Baseline copy (unchanged) | `experiment_30of30/baseline/output_baseline.csv` | same | 650,149 |
| Full old-vs-new diff | `experiment_30of30/diff_baseline_vs_candidate.txt` | 0 of 518 cases changed | |
| Code diff (experiment copy vs `code/`) | `experiment_30of30/code_changes.diff` | | |
| Reproducible metrics | `experiment_30of30/reproduce.py` → `metrics/metrics.json`, `metrics/metrics.md` | | |
| Candidate evidence ledger | `experiment_30of30/evidence_ledger_candidate.json` | | |

Isolation: the official root `output.csv`, `code/`, `submission/`, `README.md` and `log.txt` were
not modified. git shows only the new `experiment_30of30/` directory. The experiment code is a copy
in `experiment_30of30/code/`. No model or API was called.

## 1. Which file scored 28/30

* The only output in this workspace is root `output.csv`. Its SHA-256 is b88c0b30…, 650,149 bytes,
  and it is identical to `submission/output_candidate.csv` and to the file delivered and committed
  in e70970f.
* No score record, evaluator output or scored file exists in the repository, its branches or the
  container. The score "28/30" comes only from your message. I can't tell from here whether it
  was produced by b88c0b30…, which evaluator produced it, or how the 30 points are split.
* To confirm, compare the hash of the file you submitted with the hash above:
  * Windows PowerShell: `Get-FileHash "D:\Orchestrate - October\submission\output.csv" -Algorithm SHA256`
  * macOS/Linux: `sha256sum output.csv`

## 2. Baseline reproduction and sample failures

`code/main.py` regenerates b88c0b30… byte for byte. The experiment copy does too. Two runs are
identical.

| Case | Expected status | Predicted | Expected steps | Predicted | Discrepancy | Cause |
|---|---|---|---|---|---|---|
| case_04 | COMPLETE | COMPLETE | 2 | 1 | missing `VERIFICATION account_service account_004 VERIFY_RECORD_ABSENT evidence_54` | `account_004` appears in no participant file or activity row |
| case_18 | INCOMPLETE | INCOMPLETE | 14 | 13 | missing `VERIFICATION security_service audit_record_6645 VERIFY_RECORD_PRESENT ""` | the id appears nowhere |
| case_20 | INCOMPLETE | INCOMPLETE | 8 | 7 | missing `VERIFICATION security_service audit_record_5569 VERIFY_RECORD_PRESENT ""` | the id appears nowhere |

The other 29 sample cases match exactly. Field accuracy (1119/1158) is lower than step recall
(190/193) because each missing step shifts the positions of the steps after it.

## 3. Re-verification of the three missing ids

Every source was searched for exact tokens:

* all dataset CSVs (identities, identity audit log, activity, manifest, rules, requests);
* every SQL, JSON, log and text evidence file;
* `pdftotext` output of every PDF;
* screenshots, by re-reading the images (evidence_37 and evidence_60 were re-checked visually for
  this experiment).

The ids `account_004`, `audit_record_6645` and `audit_record_5569` occur nowhere outside
`sample_output.csv`. Earlier substring hits such as `support_5569` were different resources.

Additional checks:

* **Activity windows.** The activity around cases 18 and 20 contains no RETAIN action for any
  security record. The worker never touched these audit records.
* **Numbering.** 65 of 416 known audit records share a number with an unrelated resource, which
  is what random draws from about 9,000 values produce. Only 1 is in the same case. Audit-record
  ids can't be derived from other ids.
* **evidence_54.** The golden case_04 step cites evidence_54, a failover capture of
  `account_005/user_05` plus decoys. It proves `account_004` absent only by omission, so the
  golden id itself comes from organizer truth, not from participant data.
* **Hand-crafted accounts.** All 8 hand-crafted sample users follow `user_0N → account_00N`, and
  seven of those ids (001, 002, 005, 006, 007, 008, 010) appear in evidence or activity.
  `account_004` is the exception. Recovering it would mean writing an id that exists in no
  participant file. That is the "invented identifier" the task constraints forbid, so it was not
  done (see H1).

**Conclusion: none of the three steps can be recovered from participant data.**

## 4. Reassessment of the high-risk decisions

| Item | Evidence re-checked | Decision |
|---|---|---|
| Missing audit records | Every one of the 22 generated sample cases has exactly one account and one audit record in golden. In the evaluation output, all 152 COMPLETE generated cases have one. The 91 INCOMPLETE cases without one cluster in 10 batches whose main file omits most audit records; for example, evidence_89 holds 1 audit record for 10 cases. Sample batch 09-26 (cases 18 and 20) behaves the same way. | Unrecoverable: the golden answers very likely contain an uncited audit record that has no participant-visible id. |
| case_63 | evidence_60 shows "Soo-jin Park, sj.park@naver.com, +82 10-5837-9451". Neither identifier belongs to any known user. Catalog activity_4008 explicitly links `marketing_001` to `kwame-mensah@proton.me` and `user_09`. All 12 artifact cases follow the same artifact + catalog-link pattern, with audit_at = artifact capture + 26 h. | Keep INCOMPLETE: `marketing_001` REPORT_REMAINING_RECORD. |
| case_45 | evidence_37 (07:00) is the artifact (catalog link 07:07, audit_at = 07:00 + 26 h). evidence_55 (06:30) names only `audit_record_003`. On the screenshot all six events belong to `user_03`, but ids 001, 002, 004, 005 and 006 appear in no other file or activity row, while real generated records always recur elsewhere. | Keep `audit_record_003` only, cited to evidence_37, the latest admissible observation. |
| case_34 | `support_9538` belongs to `user_11`'s August episode (activity_3489 on 08-28, evidence_01 on 09-15) and appears only as a context row in evidence_04. The episode rule is validated on samples case_07 (`object_19`) and case_17 (`account_6523`). | Keep it excluded. |
| Tombstones 543/545/547/549 | evidence_52's `customer_data_*` tables hold only audit and tombstone rows. In every other `customer_data_*` capture, accounts live in the same tables (replicas keep drifted account rows), so absence from them proves deletion. The old `_cite_case_file` heuristic had **no** sample support: disabling it changes no sample metric. | Citation stays evidence_52, now derived from coverage (E2). |
| Low-margin pairs | 14 zero-margin pairs: 12 account pairs (59/60, 104/106, 203/205, 225/226, 243/244, 294/295, 345/346, 347/349, 404/406, 408/409, 411/412, 527/529) and 2 analytics pairs (155/157, 233/235). Each pair shares reported_at and reported status, and the two candidates have identical or missing phases. | Irreducible; see probes P2–P5. The case-number tie-break is a coin flip (P3). |
| legal_hold_1745 (case_287) | The RETAIN phase shift was miscalibrated (E1). | Resolved: margin 0.1 → 3.1, assignment unchanged. |
| Status/step consistency | Validator reports 0 errors. Every finding and every COMPLETE verification cites a file id, matching golden. Intent: the 10 NA cases (533–542) are correction, opt-out, export, access and login requests. Every other request explicitly asks for deletion, including the multilingual and "I no longer use…" texts. | No change. |

## 5. Controlled experiments

Each experiment followed the same steps: hypothesis → predicted rows → minimal change in
`experiment_30of30/code` → full run → diff → regression suite → keep or reject.

| Id | Hypothesis | Measurement | Predicted rows | Outcome | Decision |
|---|---|---|---|---|---|
| E1 | The RETAIN_RECORD phase shift (19.5) is miscalibrated. | 8 identity-linked legal holds need 18.73–19.73 (mean about 19.0, sd 0.35). | Only the keyless legal holds (case_98, case_287) | 0 rows changed; margins 2.6 → 5.6 (case_98) and 0.1 → 3.1 (case_287) | **Kept** (calibration) |
| E2 | A `customer_data_*` record capture covers accounts even without account rows. This replaces the unsupported `_cite_case_file` heuristic. | Account rows are present in every generated `customer_data_*` replica capture. | Citations of 543/545/547/549 | 0 rows changed; 8 ledger states now cite evidence_52 coverage instead of evidence_02 | **Kept** (principled rule, same output) |
| E3 | Post-report templates need separate shifts. | Measured vs the primary-delete phase: restore 8.17 (n=12), connector 8.51 (n=37), final 8.60 (n=247). | Ties whose phase comes from checks | 0 rows changed | Rejected (no effect, extra constants) |
| P1 | Does `_cite_case_file` have sample support? | Disabled it: samples unchanged at 190/193. | 4 tombstone cases | Only 543/545/547/549 depend on it | Motivated E2 |
| P2 | Row order in evidence files reveals the owning case. | Rows are sorted by resource_id. | Ties | No signal | Rejected |
| P3 | The lower case number gets the earlier primary-profile delete. | 79 agree, 79 disagree on identity-linked same-time pairs | Ties | Coin flip | Documented as arbitrary |
| P4 | The run phase is a function of case, user or timestamps. | No relationship | Ties | No signal | Rejected |
| P5 | Analytics product content matches the owner's other records. | Own case 1/82 vs random case 6/82 | Analytics ties | No signal | Rejected |
| P6 | Audit-record ids are derivable from other resource numbers. | Overlaps are chance (65/416, 1 in the same case) | 91 + 2 cases | No signal | Rejected |

Other shifts were confirmed within 0.1 s: ANONYMIZE 19.33 (n=104), REDACT 19.90 (n=36),
DISABLE 20.33 (n=36), BLOCK 20.91 (n=36) and tombstone REDACT 19.40 (n=5).

### Hypotheses not adopted (need your decision or have no support)

| Id | Change | Support | Why not adopted | Possible effect |
|---|---|---|---|---|
| H1 | Add `account_003` to case_45 and `account_009` to case_63 (hand-crafted convention). | 8/8 hand-crafted sample users have `account_0NN`; `account_004` is the exact precedent of an id absent from data. Users 03 and 09 have no hand-crafted sample. | It writes identifiers that exist in no participant file, which breaks "Do not invent identifiers". case_45 is COMPLETE and would also need a citation, and no admissible file is known (evidence_54 predates the request). | +1 step in each case if the golden answers contain them, −1 if not. Needs your explicit authorization. |
| H2 | Report all six case_45 audit records. | The screenshot shows six `user_03` events. | Five ids appear nowhere else; the catalog link and the PDF single out 003. | ±5 steps in case_45 |
| H3 | Cite evidence_55 for case_45. | The PDF names `audit_record_003`. | evidence_37 is the later admissible observation and is the artifact the case timing is built on. | ±1 field |

## 6. Metrics (from `reproduce.py`; all local, none official)

| Metric | Baseline | Candidate |
|---|---|---|
| Official score | 28/30 (user-reported; scored file not confirmed) | not available locally |
| Sample status accuracy | 32/32 | 32/32 |
| Sample step precision | 190/190 | 190/190 |
| Sample step recall | 190/193 | 190/193 |
| Sample step F1 | 0.9922 | 0.9922 |
| Sample field accuracy by position | 1119/1158 | 1119/1158 |
| Sample sequence accuracy | 29/32 | 29/32 |
| Rows / unique case ids | 518 / 518 | 518 / 518 |
| Status counts | COMPLETE 153, INCOMPLETE 355, NA 10 | same |
| Steps / citations | 3254 / 1246 | 3254 / 1246 |
| Schema/contract errors (validator) | 0 | 0 |
| Citation admissibility (validator: cap ≤ audit_at) | 0 violations | 0 violations |
| Custom cases | 4/4 | 4/4 |
| Attribution holdout | 80/80 | 80/80 |
| History cross-check | 117/117 | 117/117 |
| Deterministic (two runs) | yes | yes |
| Cases changed vs baseline | — | 0/518 |

## 7. Where the remaining error most likely is (estimate, not measured)

This assumes the hidden answers follow the same regularities as the 32 samples:

* **91 INCOMPLETE cases** probably each contain one uncited `VERIFY_RECORD_PRESENT` for an audit
  record whose id is in no participant file. This is the dominant loss. It affects steps and exact
  sequences, not statuses.
* **2 hand-crafted accounts** (case_45, case_63) are blocked by the no-invented-ids rule (H1).
* **14 coin-flip pairs**: about half are expected to be swapped, which is about 7 pairs × 2 cases,
  each with one wrong verification step.

Under these assumptions:

| Measure | Estimate |
|---|---|
| Step precision | about 0.996 |
| Step recall | about 0.968 |
| Exact-sequence accuracy | about 0.79 |
| Status accuracy | essentially unaffected (none of the items above changes a status) |

None of these items can be fixed from participant-visible evidence without guessing or inventing
identifiers.

## 8. Promotion gate and recommendation

* The candidate is byte-identical to the scored baseline, so no promotion applies.
* **Keep the current release `output.csv`.** Nothing measured or evidence-justified would change it.
* E1 and E2 make the solver's reasoning more principled and leave its output unchanged. They live
  only in the experiment copy. Porting them to `code/` is optional and was not done without your
  authorization.
* The only change with sample precedent that could move the hidden score is H1. It requires
  inventing two identifiers, so it is your call. Even if you allow it, case_45 has no known
  admissible citation for the added step.

Reproduce everything with `python3 experiment_30of30/reproduce.py` (about 75 s, standard library only).
