"""Deletion-audit solver: from observations to the per-case status and ordered steps.

Pipeline per request
  1. intent      - does the request authorize deletion (intent.py)
  2. identity    - resolve current + historical identifiers of the subject
  3. discovery   - resources linked to the subject by identifiers in evidence or by the
                   data catalog, scoped to the request episode
  4. attribution - keyless resources (deleted accounts, anonymized analytics, legal holds)
                   that only appear in deletion-worker activity are assigned jointly to the
                   cases whose deletion window contains the activity
  5. state       - latest admissible observation at audit_at (file captured <= audit_at)
  6. policy      - deletion_rules.csv treatment -> findings / remediation / verification
"""
from __future__ import annotations

import csv
import itertools
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

import evidence
import intent

# --------------------------------------------------------------------------- policy
RULE_ACTION = {
    "USER_PROFILE": "DELETE",
    "SECONDARY_IDENTIFIER": "DELETE",
    "SEARCH_INDEX_ENTRY": "DELETE",
    "CACHE_ENTRY": "DELETE",
    "SUPPORT_MESSAGE": "REDACT",
    "SUPPORT_ATTACHMENT": "DELETE",
    "ANALYTICS_PROFILE": "ANONYMIZE",
    "PRODUCT_TELEMETRY": "ANONYMIZE",
    "USER_EXPORT": "DELETE",
    "MARKETING_PROFILE": "DELETE",
    "EMAIL_DELIVERY_LOG": "REDACT",
    "SECURITY_AUDIT_LOG": "RETAIN",
    "LEGAL_HOLD_RECORD": "RETAIN",
    "BACKUP_ARCHIVE": "BLOCK",
    "ACCOUNT_TOMBSTONE": "REDACT",
    "DATA_FLOW": "DISABLE",
}
ACTIVITY_ACTION = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE"}

OP_ORDER = {
    "FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW",
                "REPORT_RESTORE_RISK"],
    "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD",
                    "ANONYMIZE_RECORD"],
    "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                     "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"],
}
STATE_OF = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
RESULT_STATUS = {"SUCCESS": "COMPLETE", "PARTIAL": "PARTIALLY_COMPLETE", "FAILED": "NOT_STARTED"}

# deletion-worker timing relative to reported_at (minutes), measured on identity-linked cases
ACCOUNT_WINDOW = (-77, -42)       # "submitted primary profile" deletes
WORKER_WINDOW = (-80, 180)        # every deletion-worker action of the case
EPISODE_SLACK = timedelta(days=0)  # observations before requested_at do not open an episode


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def minutes(a: str, b: str) -> float:
    return (ts(a) - ts(b)).total_seconds() / 60


def linking_keys(keys) -> set:
    return {k for k in keys if k and not k.startswith(("name:", "rid:"))}


def rid_num(rid: str) -> int:
    m = re.search(r"(\d+)$", rid)
    return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------- identities
class Identities:
    def __init__(self, dataset_dir):
        with open(os.path.join(dataset_dir, "user_identities.csv"), encoding="utf-8") as fh:
            self.users = {r["user_id"]: r for r in csv.DictReader(fh)}
        with open(os.path.join(dataset_dir, "user_audit_log.csv"), encoding="utf-8") as fh:
            self.audit = list(csv.DictReader(fh))
        self.keys = {}
        for u, r in self.users.items():
            ks = {"user:" + u, evidence.norm_key(r["email"]), evidence.norm_key(r["email_hash"]),
                  evidence.norm_key(r["phone"])}
            self.keys[u] = {k for k in ks if k}
        for a in self.audit:
            for v in (a["old_value"], a["new_value"]):
                k = evidence.norm_key(v)
                if k:
                    self.keys[a["user_id"]].add(k)
        self.owner = defaultdict(set)
        for u, ks in self.keys.items():
            for k in ks:
                self.owner[k].add(u)

    def resolve(self, email):
        return sorted(self.owner.get(evidence.norm_key(email), set()))

    def owners(self, keys):
        out = set()
        for k in linking_keys(keys):
            out |= self.owner.get(k, set())
        return out


# --------------------------------------------------------------------------- solver
class Solver:
    def __init__(self, dataset_dir):
        self.dataset_dir = dataset_dir
        self.store = evidence.load(dataset_dir)
        self.ids = Identities(dataset_dir)
        with open(os.path.join(dataset_dir, "system_activity.csv"), encoding="utf-8") as fh:
            self.activity = list(csv.DictReader(fh))
        self.obs_by_rid = defaultdict(list)
        for o in self.store.obs:
            self.obs_by_rid[o["rid"]].append(o)
        self.worker_by_rid = defaultdict(list)
        for a in self.activity:
            if a["source_system"] == "deletion_worker":
                self.worker_by_rid[a["resource_id"]].append(a)
        self._link_resources()

    # ---------------------------------------------------------------- linking
    def _link_resources(self):
        """resource -> users, from identity keys in any evidence and catalog links."""
        self.rid_users = defaultdict(set)
        self.user_rids = defaultdict(set)
        for o in self.store.obs:
            for u in self.ids.owners(o["keys"]):
                self.rid_users[o["rid"]].add(u)
        catalog_re = re.compile(
            r"(?:linked|confirmed) (\S+) \((.*?)\) (?:to|belongs only to) (\S+) and (user_\d+)")
        self.catalog = {}
        for a in self.activity:
            if a["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            m = catalog_re.search(a["details"])
            if m:
                rid, label, email, user = m.groups()
                self.rid_users[rid].add(user)
                self.catalog[rid] = {"user": user, "email": email, "label": label, "time": a["event_time"],
                                     "activity_id": a["activity_id"]}
        for rid, us in self.rid_users.items():
            for u in us:
                self.user_rids[u].add(rid)

    # ---------------------------------------------------------------- cases
    @staticmethod
    def load_requests(path):
        with open(path, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def solve_all(self, requests):
        cases = []
        for r in requests:
            c = dict(r)
            c["users"] = self.ids.resolve(r["email"])
            c["user"] = c["users"][0] if c["users"] else None
            c["deletion"] = intent.authorizes_deletion(r["request_text"])
            c["resources"] = {}
            cases.append(c)
        self.cases = {c["case_id"]: c for c in cases}
        dcases = [c for c in cases if c["deletion"] and c["user"]]
        for c in dcases:
            self._discover_linked(c)
        self._assign_keyless(dcases)
        return {c["case_id"]: self.build(c) for c in cases}

    # ---------------------------------------------------------------- discovery
    def _discover_linked(self, c):
        u, req, audit, rep = c["user"], c["requested_at"], c["audit_at"], c["reported_at"]
        start = (ts(req) - EPISODE_SLACK).strftime("%Y-%m-%dT%H:%M:%SZ")
        for rid in sorted(self.user_rids.get(u, ())):
            system = evidence.system_of(rid)
            obs = [o for o in self.obs_by_rid.get(rid, []) if o["ts"] <= audit]
            # context rows on a screenshot (not the record the artifact is about) do not
            # open a resource on their own
            in_window = [o for o in obs if o["ts"] >= start and o.get("featured", True)]
            acts = self.worker_by_rid.get(rid, [])
            win_acts = [a for a in acts if WORKER_WINDOW[0] <= minutes(a["event_time"], rep) <= WORKER_WINDOW[1]]
            cat = self.catalog.get(rid)
            cat_in = cat is not None and start <= cat["time"] <= audit
            if system == "account_service":
                primary = [a for a in win_acts if "primary profile" in a["details"]
                           and ACCOUNT_WINDOW[0] <= minutes(a["event_time"], rep) <= ACCOUNT_WINDOW[1]]
                if primary or in_window or cat_in:
                    c["resources"][rid] = "linked:" + ("primary_delete" if primary else "observed")
            elif in_window or win_acts or cat_in:
                c["resources"][rid] = "linked"

    # ---------------------------------------------------------------- keyless attribution
    def _keyless_kind(self, a):
        if a["target_system"] == "account_service" and a["event_type"] == "DELETE_RECORD":
            return "account"
        if a["target_system"] == "analytics_platform":
            return "analytics"
        if a["target_system"] == "legal_service":
            return "legal"
        return None

    @staticmethod
    def _slot(a):
        """Offset window (minutes from reported_at) of a deletion-worker message template,
        measured on identity-linked cases."""
        if a["details"].startswith("Policy action completed"):
            return {
                "ANONYMIZE_RECORD": (-31, -27), "RETAIN_RECORD": (-31, -27),
                "REDACT_RECORD": (-31, -19), "DISABLE_DATA_FLOW": (-16, -6),
                "BLOCK_RESTORE": (-9, -2),
            }.get(a["event_type"], WORKER_WINDOW)
        if "reported the requested operation as completed" in a["details"]:
            return (-10, 10)
        return (30, 140)

    def _has_kind(self, c, kind):
        sysname = {"account": "account_service", "analytics": "analytics_platform",
                   "legal": "legal_service"}[kind]
        return any(evidence.system_of(r) == sysname for r in c["resources"])

    def _provisional_complete(self, c):
        for rid in c["resources"]:
            st = self.state(rid, c)
            if st and self._finding_for(rid, st, c):
                return False
        return True

    # ------------------------------------------------------------ workflow timing fingerprint
    # Every deletion-worker run stamps all of its actions with the same sub-minute phase:
    # measured on identity-linked cases, (offset_seconds + shift) mod 30 is constant within a
    # case (spread <= 2 s) for the primary-profile delete, the policy actions completed before
    # the report and the final results received after it. The phase links keyless records
    # (deleted accounts, anonymized analytics) to the run that processed them.
    PHASE_SHIFT = {"primary": 0.0, "after": 8.6, "ANONYMIZE_RECORD": 19.4, "RETAIN_RECORD": 19.0,
                   "REDACT_RECORD": 19.8, "DISABLE_DATA_FLOW": 20.3, "BLOCK_RESTORE": 20.9}
    PHASE_TOL = 2.5

    GENERATED_TEMPLATES = ("Deletion worker submitted primary profile", "Policy action completed",
                           "Deletion worker received the final result", "Connector status check",
                           "Restore-access check")

    def _phase_of(self, a, c):
        if not a["details"].startswith(self.GENERATED_TEMPLATES):
            return None
        d = (ts(a["event_time"]) - ts(c["reported_at"])).total_seconds()
        if "primary profile" in a["details"]:
            shift = self.PHASE_SHIFT["primary"]
        elif a["details"].startswith("Policy action completed"):
            shift = self.PHASE_SHIFT.get(a["event_type"], 20.0)
            if a["target_system"] == "identity_service":
                # tombstone redaction runs in the first policy slot, with the anonymization
                shift = self.PHASE_SHIFT["ANONYMIZE_RECORD"]
        elif d > 0:
            shift = self.PHASE_SHIFT["after"]
        else:
            return None
        return (d + shift) % 30

    @staticmethod
    def _circ_dist(a, b, m=30):
        x = abs(a - b) % m
        return min(x, m - x)

    def case_phase(self, c):
        """Circular mean phase of the case's own worker activity (None when unknown)."""
        import cmath
        import math
        vals = []
        for rid, how in c["resources"].items():
            for a in self.worker_by_rid.get(rid, []):
                d = minutes(a["event_time"], c["reported_at"])
                if not (WORKER_WINDOW[0] <= d <= WORKER_WINDOW[1]):
                    continue
                p = self._phase_of(a, c)
                if p is not None:
                    vals.append(p)
        if not vals:
            return None
        z = sum(cmath.exp(2j * math.pi * v / 30) for v in vals) / len(vals)
        return (cmath.phase(z) / (2 * math.pi) * 30) % 30

    def _assign_keyless(self, dcases):
        self.keyless_report = []
        for kind in ("account", "analytics", "legal"):
            self._assign_kind(kind, dcases)

    def _assign_kind(self, kind, dcases):
        rmap = defaultdict(list)  # rid -> [case]
        acts = {}
        for a in self.activity:
            if a["source_system"] != "deletion_worker" or self.rid_users.get(a["resource_id"]):
                continue
            if self._keyless_kind(a) != kind:
                continue
            for c in dcases:
                d = minutes(a["event_time"], c["reported_at"])
                if kind == "account" and "primary profile" in a["details"]:
                    ok = (ACCOUNT_WINDOW[0] <= d <= ACCOUNT_WINDOW[1]
                          and RESULT_STATUS[a["reported_result"]] == c["reported_status"])
                elif kind == "account":
                    # other worker templates report the account delete at reported_at itself
                    ok = -10 <= d <= 10
                else:
                    lo, hi = self._slot(a)
                    ok = lo <= d <= hi
                if ok and not self._has_kind(c, kind) and a["event_time"] <= c["audit_at"]:
                    rmap[a["resource_id"]].append(c)
                    acts[a["resource_id"]] = a
        phases = {c["case_id"]: self.case_phase(c) for cs in rmap.values() for c in cs}
        # connected components of resources sharing candidate cases
        seen = set()
        for rid in sorted(rmap):
            if rid in seen:
                continue
            stack, comp_r, comp_c = [rid], set(), set()
            while stack:
                r = stack.pop()
                if r in comp_r:
                    continue
                comp_r.add(r)
                for c in rmap[r]:
                    if c["case_id"] in comp_c:
                        continue
                    comp_c.add(c["case_id"])
                    for r2, cs2 in rmap.items():
                        if any(x["case_id"] == c["case_id"] for x in cs2):
                            stack.append(r2)
            seen |= comp_r
            rids = sorted(comp_r)
            cids = sorted(comp_c, key=lambda x: int(x.split("_")[1]))
            assign, detail = self._best_assignment(kind, rids, cids, rmap, acts, phases)
            for r, cid in assign.items():
                self.cases[cid]["resources"][r] = "keyless:%s:%d/%d:%s" % (
                    kind, len(rids), len(cids), detail[(r, cid)])
            self.keyless_report.append({"kind": kind, "resources": rids, "cases": cids,
                                        "assignment": assign, "detail": {f"{k[0]}->{k[1]}": v for k, v in detail.items()}})

    def _score(self, kind, rid, c, a=None, phase=None):
        s = 0.0
        audit = c["audit_at"]
        obs = [o for o in self.obs_by_rid.get(rid, []) if o["ts"] <= audit]
        files = {o["eid"] for o in obs}
        if files:
            linked_files = set()
            for r in c["resources"]:
                for o in self.obs_by_rid.get(r, []):
                    if o["ts"] <= audit and o["ts"] >= c["requested_at"]:
                        linked_files.add(o["eid"])
            if files & linked_files:
                s += 3
            elif linked_files:
                s -= 1
        # a COMPLETE case needs citable proof for every record, so the evidence includes the
        # record; an INCOMPLETE case's compliant record usually appears only in activity
        admissible = [o for o in obs if o["cap"] <= audit]
        complete = self._provisional_complete(c)
        if complete and admissible:
            s += 1
        if not complete and not admissible:
            s += 1
        tag = "nophase"
        if a is not None and phase is not None:
            p = self._phase_of(a, c)
            if p is not None:
                dist = self._circ_dist(p, phase)
                if dist <= self.PHASE_TOL:
                    # residual sd is ~0.5 s per activity, so closer phases are clearly better
                    s += 10 - 3 * dist
                    tag = "phase_match:%.1f" % dist
                else:
                    s -= 10
                    tag = "phase_mismatch:%.1f" % dist
        return s, tag

    def _best_assignment(self, kind, rids, cids, rmap, acts, phases):
        allowed = {rid: {c["case_id"] for c in rmap[rid]} for rid in rids}
        scores, tags = {}, {}
        for rid in rids:
            for cid in allowed[rid]:
                scores[(rid, cid)], tags[(rid, cid)] = self._score(
                    kind, rid, self.cases[cid], acts[rid], phases.get(cid))
        best, best_val = {}, None
        margin = {}
        # an assignment is worth making when its evidence score is not negative
        if len(rids) <= 7 and len(cids) <= 7:
            options = [[None] + sorted(allowed[r], key=lambda x: int(x.split("_")[1])) for r in rids]
            evaluated = []
            for combo in itertools.product(*options):
                used = [x for x in combo if x]
                if len(used) != len(set(used)):
                    continue
                val = (sum(5 + scores[(r, x)] for r, x in zip(rids, combo) if x),
                       self._order_agreement(rids, combo, acts))
                evaluated.append((val[0], combo))
                if best_val is None or val > best_val:
                    best_val, best = val, {r: x for r, x in zip(rids, combo) if x}
            # confidence margin: best total minus best total with a different case for rid
            for i, r in enumerate(rids):
                alt = [v for v, combo in evaluated if combo[i] != best.get(r)]
                margin[r] = round(best_val[0] - max(alt), 1) if alt else 99.0
        else:
            for rid in sorted(rids, key=lambda r: -max(scores[(r, x)] for x in allowed[r])):
                opts = sorted(allowed[rid] - set(best.values()),
                              key=lambda x: (-scores[(rid, x)], int(x.split("_")[1])))
                if opts and scores[(rid, opts[0])] + 5 > 0:
                    best[rid] = opts[0]
        return best, {k: "%s:margin=%s" % (tags[k], margin.get(k[0], "na")) for k in best.items()}

    def _order_agreement(self, rids, combo, acts):
        """Deterministic last-resort tie-break: earlier activity <-> lower case number."""
        pairs = [(acts[r]["event_time"], int(x.split("_")[1])) for r, x in zip(rids, combo) if x]
        agree = 0
        for (t1, c1), (t2, c2) in itertools.combinations(pairs, 2):
            agree += (t1 < t2) == (c1 < c2)
        return agree

    # ---------------------------------------------------------------- state
    def state(self, rid, c):
        """State of rid at audit_at.

        Returns dict(present, exposed, active, eid, cat) where eid is a citable evidence id
        (captured at or before audit_at) or None when the only knowledge is uncitable.
        """
        audit = c["audit_at"]
        obs = [o for o in self.obs_by_rid.get(rid, []) if o["ts"] <= audit]
        admissible = [o for o in obs if o["cap"] <= audit]
        system = evidence.system_of(rid)
        if admissible:
            latest = max(admissible, key=lambda o: (o["ts"], o["cap"], o["eid"]))
            return {"present": latest["present"], "exposed": latest["exposed"],
                    "active": latest["active"], "eid": latest["eid"], "src": latest["src"],
                    "ts": latest["ts"]}
        # complete-table captures prove an account is gone; other collections in the
        # snapshot bundles/inventories are partial extracts and prove nothing by omission
        cov = [cv for cv in self.store.coverage
               if system == "account_service" and cv["system"] == system and cv["ts"] <= audit
               and cv["cap"] <= audit and cv["ts"] >= c["requested_at"] and rid not in cv["rids"]]
        if cov:
            cv = max(cov, key=lambda x: (x["ts"], x["cap"], x["eid"]))
            return {"present": False, "exposed": None, "active": None, "eid": cv["eid"],
                    "src": "coverage:" + cv["src"], "ts": cv["ts"]}
        if obs:
            latest = max(obs, key=lambda o: (o["ts"], o["cap"]))
            return {"present": latest["present"], "exposed": latest["exposed"],
                    "active": latest["active"], "eid": None, "src": "uncitable:" + latest["src"],
                    "ts": latest["ts"]}
        acts = [a for a in self.worker_by_rid.get(rid, []) if a["event_time"] <= audit]
        if acts:
            # only workflow claims are known; the record is assumed in the requested state
            kind = acts[-1]["event_type"]
            if kind in ("CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"):
                return {"present": True, "exposed": None, "active": True, "eid": acts[-1]["activity_id"],
                        "src": "activity:" + kind, "ts": acts[-1]["event_time"]}
            return {"present": system in ("legal_service", "security_service"), "exposed": False,
                    "active": False, "eid": None, "src": "activity_claim:" + kind,
                    "ts": acts[-1]["event_time"]}
        return None

    def category(self, rid):
        obs = self.obs_by_rid.get(rid, [])
        cats = [o["cat"] for o in obs if o.get("cat")]
        system = evidence.system_of(rid)
        for pref in ("SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER"):
            if pref in cats:
                return pref
        acts = self.worker_by_rid.get(rid, [])
        if system in ("support_platform", "analytics_platform"):
            ops = {ACTIVITY_ACTION.get(a["event_type"]) for a in acts} - {None}
            if ops == {"DELETE"}:
                return "SUPPORT_ATTACHMENT" if system == "support_platform" else "SECONDARY_IDENTIFIER"
        if cats:
            return max(sorted(set(cats)), key=cats.count)
        if system == "support_platform":
            return "SUPPORT_MESSAGE"
        return evidence.SYSTEM_CATEGORY.get(system)

    def _finding_for(self, rid, st, c):
        action = RULE_ACTION.get(self.category(rid))
        if action == "DELETE":
            return bool(st["present"])
        if action in ("REDACT", "ANONYMIZE"):
            return st["present"] is not False and bool(st["exposed"])
        if action in ("DISABLE", "BLOCK"):
            return bool(st["active"])
        return False

    # ---------------------------------------------------------------- steps
    def build(self, c):
        if not c["deletion"]:
            return {"status": "NOT_APPLICABLE", "steps": [], "ledger": {"reason": "request does not authorize deletion"}}
        findings, remediation, verification = [], [], []
        ledger = {"user": c["user"], "resources": {}}
        for rid in sorted(c["resources"]):
            system = evidence.system_of(rid)
            st = self.state(rid, c)
            cat = self.category(rid)
            action = RULE_ACTION.get(cat)
            ledger["resources"][rid] = {"how": c["resources"][rid], "system": system, "category": cat,
                                        "action": action, "state": st}
            if st is None or action is None:
                continue
            eid = st["eid"] or ""
            if action == "DELETE":
                if st["present"]:
                    findings.append(("REPORT_REMAINING_RECORD", system, rid, eid))
                    remediation.append(("DELETE_RECORD", system, rid))
                verification.append(("VERIFY_RECORD_ABSENT", system, rid, eid))
            elif action in ("REDACT", "ANONYMIZE"):
                if st["present"] is False:
                    continue
                vop = "VERIFY_RECORD_REDACTED" if action == "REDACT" else "VERIFY_RECORD_ANONYMIZED"
                rop = "REDACT_RECORD" if action == "REDACT" else "ANONYMIZE_RECORD"
                if st["exposed"]:
                    findings.append(("REPORT_EXPOSED_PII", system, rid, eid))
                    remediation.append((rop, system, rid))
                verification.append((vop, system, rid, eid))
            elif action == "RETAIN":
                if st["present"] is not False:
                    verification.append(("VERIFY_RECORD_PRESENT", system, rid, eid))
            elif action == "DISABLE":
                if st["active"]:
                    findings.append(("REPORT_ACTIVE_DATA_FLOW", system, rid, eid))
                    remediation.append(("DISABLE_DATA_FLOW", system, rid))
                verification.append(("VERIFY_DATA_FLOW_DISABLED", system, rid, eid))
            elif action == "BLOCK":
                if st["active"]:
                    findings.append(("REPORT_RESTORE_RISK", system, rid, eid))
                    remediation.append(("BLOCK_RESTORE", system, rid))
                verification.append(("VERIFY_RESTORE_BLOCKED", system, rid, eid))
        status = "INCOMPLETE" if findings else "COMPLETE"

        def order(phase):
            return lambda x: (OP_ORDER[phase].index(x[0]), x[1], x[2])

        steps = []
        if status == "INCOMPLETE":
            steps += [("FINDING", s, r, op, e) for op, s, r, e in sorted(findings, key=order("FINDING"))]
            steps += [("REMEDIATION", s, r, op, "") for op, s, r in sorted(remediation, key=order("REMEDIATION"))]
            steps += [("VERIFICATION", s, r, op, "") for op, s, r, e in sorted(verification, key=order("VERIFICATION"))]
        else:
            steps += [("VERIFICATION", s, r, op, e) for op, s, r, e in sorted(verification, key=order("VERIFICATION"))]
        out = [{"step_no": i, "phase": ph, "system_id": s, "resource_id": r, "operation": op,
                "state": STATE_OF[op], "evidence_id": e}
               for i, (ph, s, r, op, e) in enumerate(steps, 1)]
        return {"status": status, "steps": out, "ledger": ledger}


def write_output(results, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(results, key=lambda x: int(x.split("_")[1])):
            r = results[cid]
            w.writerow([cid, r["status"], json.dumps(r["steps"], separators=(",", ":"), ensure_ascii=False)])
