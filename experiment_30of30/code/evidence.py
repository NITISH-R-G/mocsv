"""Evidence extraction: turn every participant-facing evidence file into observations.

An observation is a dict describing what one evidence item says about one
resource at one point in time:

    eid      evidence id that proves it (file_id from evidence_files.csv)
    ts       time the state was observed (record-level time when available)
    cap      captured_at of the evidence file (manifest)
    system   system_id (account_service, search_service, ...)
    rid      resource_id
    cat      data category hint (USER_PROFILE, SUPPORT_ATTACHMENT, ...) or None
    present  True / False / None  - does the record exist
    exposed  True / False / None  - are direct identifiers still visible
    active   True / False / None  - data flow enabled / backup restorable
    keys     set of normalised identifiers linking the resource to a person
    src      short human readable description

Besides observations the extractor records "coverage" snapshots: complete
captures of a system's collection at a time. A resource missing from a
covering snapshot is absent at that time.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import sqlite3
from collections import defaultdict

import logparse

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"^user_\d+$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RID_RE = re.compile(
    r"^(account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|"
    r"legal_hold|marketing|object|cache|email_delivery|telemetry)_\w+$"
)

SYSTEM_BY_PREFIX = [
    ("audit_record_", "security_service"),
    ("data_flow_", "integration_service"),
    ("account_", "account_service"),
    ("search_", "search_service"),
    ("analytics_", "analytics_platform"),
    ("support_", "support_platform"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
    ("tombstone_", "identity_service"),
    ("legal_hold_", "legal_service"),
    ("marketing_", "marketing_platform"),
    ("object_", "object_storage"),
    ("cache_", "cache_service"),
    ("email_delivery_", "email_delivery_service"),
]

COLLECTION_SYSTEM = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "customer_analytics_reports": "analytics_platform",
    "customer_analytics_profiles": "analytics_platform",
    "security_audit_events": "security_service",
    "security_audit_records": "security_service",
    "backup_catalog": "backup_service",
    "backup_restore_entries": "backup_service",
    "connector_configurations": "integration_service",
    "connector_subscriptions": "integration_service",
    "support_conversations": "support_platform",
    "support_records": "support_platform",
    "ticket_attachments": "support_platform",
    "customer_export_jobs": "export_service",
    "customer_exports": "export_service",
    "deleted_customer_references": "identity_service",
    "retained_order_records": "legal_service",
}

COLLECTION_CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "backup_catalog": "BACKUP_ARCHIVE",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "connector_configurations": "DATA_FLOW",
    "connector_subscriptions": "DATA_FLOW",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "customer_exports": "USER_EXPORT",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}

SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "security_service": "SECURITY_AUDIT_LOG",
    "backup_service": "BACKUP_ARCHIVE",
    "integration_service": "DATA_FLOW",
    "export_service": "USER_EXPORT",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD",
    "marketing_platform": "MARKETING_PROFILE",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
}

IDENTIFIER_FIELDS = {
    "email", "phone", "email_hash", "full_name", "name", "display_name",
    "requester_email", "requester_name", "requester_phone", "owner_email",
    "owner_phone", "customer_email", "user_id",
}


def system_of(rid: str) -> str | None:
    for prefix, system in SYSTEM_BY_PREFIX:
        if rid.startswith(prefix):
            return system
    return None


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_phone(value: str) -> bool:
    return bool(re.fullmatch(r"\+?[\d\s()\-.]{7,}", value.strip())) and len(norm_phone(value)) >= 7


def norm_key(value) -> str | None:
    """Normalise a linking identifier; None when it carries no identity."""
    if value is None:
        return None
    v = str(value).strip()
    if not v or v.lower() in {"null", "none", "no direct identifier", "[redacted]"}:
        return None
    if "@" in v:
        m = EMAIL_RE.search(v)
        return "email:" + m.group(0).lower() if m else None
    if USER_RE.match(v):
        return "user:" + v
    if HASH_RE.match(v.lower()):
        return "hash:" + v.lower()
    if is_phone(v):
        return "phone:" + norm_phone(v)
    if RID_RE.match(v):
        return "rid:" + v
    return "name:" + v.lower()


def keys_from_text(text: str) -> set:
    out = set()
    for m in EMAIL_RE.findall(text or ""):
        out.add("email:" + m.lower())
    return out


def record_keys(rec: dict) -> set:
    """Identity keys carried by a record (content + references)."""
    out = set()
    for k, v in rec.items():
        if isinstance(v, (dict, list)):
            continue
        if v is None:
            continue
        if k in IDENTIFIER_FIELDS or k in {
            "customer_reference", "subject_reference", "subject_selector",
            "uploaded_by", "lookup_value",
        }:
            nk = norm_key(v)
            if nk:
                out.add(nk)
        elif k in {"message_text", "message_excerpt"}:
            out |= keys_from_text(str(v))
    return out


def direct_identifiers(rec: dict) -> bool:
    """Whether a record still exposes direct identifiers (name/email/phone/hash/user id)."""
    for k, v in rec.items():
        if k in IDENTIFIER_FIELDS and v not in (None, "", "null"):
            return True
        if k == "message_text" and v and "[REDACTED]" not in str(v) and EMAIL_RE.search(str(v)):
            return True
    return False


class Store:
    def __init__(self, dataset_dir: str):
        self.dataset_dir = dataset_dir
        self.manifest = {}
        with open(os.path.join(dataset_dir, "evidence_files.csv"), encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                self.manifest[row["file_id"]] = row
        self.path_to_eid = {row["file_path"]: fid for fid, row in self.manifest.items()}
        self.obs: list[dict] = []
        self.coverage: list[dict] = []
        self.catalog_links: list[dict] = []  # explicit resource-identity links

    # ------------------------------------------------------------------ helpers
    def add(self, eid, ts, system, rid, *, present=None, exposed=None, active=None,
            keys=(), cat=None, src="", extra=None):
        if not rid:
            return
        system = system or system_of(rid)
        o = {
            "eid": eid,
            "cap": self.manifest[eid]["captured_at"] if eid in self.manifest else ts,
            "ts": ts,
            "system": system,
            "rid": rid,
            "cat": cat or SYSTEM_CATEGORY.get(system),
            "present": present,
            "exposed": exposed,
            "active": active,
            "keys": {k for k in keys if k},
            "src": src,
        }
        if extra:
            o.update(extra)
        self.obs.append(o)

    def add_coverage(self, eid, ts, system, rids, src):
        self.coverage.append({
            "eid": eid, "ts": ts, "cap": self.manifest[eid]["captured_at"],
            "system": system, "rids": set(rids), "src": src,
        })

    # ------------------------------------------------------------------ loaders
    def load_all(self):
        for eid, row in sorted(self.manifest.items()):
            path = os.path.join(self.dataset_dir, row["file_path"])
            ext = os.path.splitext(path)[1].lower()
            if ext == ".sql":
                self.load_sql(eid, path)
            elif ext == ".json":
                self.load_json(eid, path)
            elif ext == ".log":
                self.load_log(eid, path)
            elif ext == ".txt":
                self.load_record_lines(eid, path)
            # images / pdfs handled by load_transcriptions
        self.load_transcriptions(os.path.join(os.path.dirname(__file__), "evidence_transcriptions.json"))
        return self

    # SQL ------------------------------------------------------------------
    def load_sql(self, eid, path):
        con = sqlite3.connect(":memory:")
        with open(path, encoding="utf-8") as fh:
            con.executescript(fh.read())
        cap = self.manifest[eid]["captured_at"]
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        if "snapshot_scope" in tables:
            self._load_inventory(eid, con, tables)
            return
        # replica style: each table is one copy of the data
        by_system = defaultdict(set)
        systems_seen = set()
        for t in tables:
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            rows = con.execute(f'select * from "{t}"').fetchall()
            for row in rows:
                rec = dict(zip(cols, row))
                if "record_json" in rec:
                    data = json.loads(rec["record_json"])
                    coll = rec["collection_name"]
                    system = rec["origin_system"]
                else:
                    data = dict(rec)
                    if t.startswith("search_documents"):
                        coll, system = "customer_search_documents", "search_service"
                    else:
                        coll, system = "customer_profiles", "account_service"
                rid = rec["resource_id"]
                systems_seen.add(system)
                by_system[system].add(rid)
                self._add_record(eid, rec.get("updated_at") or cap, system, coll, rid, data,
                                 src=f"{t}")
        # every system present in the capture is treated as fully captured; the generic
        # customer_data record tables also hold account profiles (replicas keep drifted
        # account rows there), so a capture without account rows proves accounts are gone
        if any(t.startswith("customer_data_") for t in tables):
            systems_seen.add("account_service")
        topo = re.search(r"Database topology: (\w+)", open(path, encoding="utf-8").read())
        for system in systems_seen:
            self.add_coverage(eid, cap, system, by_system[system],
                              f"sql:{topo.group(1) if topo else ''}")

    def _load_inventory(self, eid, con, tables):
        scope = dict(con.execute("select origin_system, captured_at from snapshot_scope").fetchall())
        table_coll = {
            "customer_profiles": "customer_profiles",
            "customer_search_documents": "customer_search_documents",
            "customer_analytics_profiles": "customer_analytics_profiles",
            "support_records": "support_records",
            "customer_exports": "customer_exports",
            "connector_subscriptions": "connector_subscriptions",
            "backup_restore_entries": "backup_restore_entries",
            "security_audit_records": "security_audit_records",
        }
        by_system = defaultdict(set)
        for t, coll in table_coll.items():
            if t not in tables:
                continue
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            for row in con.execute(f'select * from "{t}"').fetchall():
                rec = dict(zip(cols, row))
                system = COLLECTION_SYSTEM[coll]
                by_system[system].add(rec["resource_id"])
                self._add_record(eid, rec.get("updated_at"), system, coll, rec["resource_id"], rec,
                                 src=f"inventory:{t}")
        for system, ts in scope.items():
            self.add_coverage(eid, ts, system, by_system[system], "inventory")

    # generic record -> observation
    def _add_record(self, eid, ts, system, coll, rid, data, src=""):
        cat = COLLECTION_CATEGORY.get(coll)
        keys = record_keys(data)
        present = True
        exposed = None
        active = None
        if cat in ("ANALYTICS_PROFILE", "SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE", "USER_PROFILE",
                   "SEARCH_INDEX_ENTRY", "USER_EXPORT", "SUPPORT_ATTACHMENT"):
            flat = dict(data)
            if isinstance(data.get("document"), dict):
                flat.update(data["document"])
            exposed = direct_identifiers(flat)
            keys |= record_keys(flat)
        if cat == "DATA_FLOW":
            en = data.get("enabled")
            active = str(en).lower() in ("1", "true")
        if cat == "BACKUP_ARCHIVE":
            ra = data.get("restore_allowed")
            active = str(ra).lower() in ("1", "true")
        if cat == "USER_EXPORT" and "download_available" in data:
            present = bool(data["download_available"])
        if cat == "USER_PROFILE" and data.get("account_status") == "DELETED":
            present = False
        self.add(eid, ts, system, rid, present=present, exposed=exposed, active=active,
                 keys=keys, cat=cat, src=src, extra={"data": data})

    # JSON -----------------------------------------------------------------
    def load_json(self, eid, path):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        cap = self.manifest[eid]["captured_at"]
        if "snapshots" in d:
            for snap in d["snapshots"]:
                coll = snap["collection"]
                ts = snap["captured_at"]
                if coll == "operational_record_history":
                    for r in snap["records"]:
                        self._add_record_lookup(eid, r.get("recorded_at") or ts, r,
                                                src=f"history:{snap['data_store']}")
                    continue
                system = snap["data_store"]
                rids = []
                for r in snap["records"]:
                    rids.append(r["resource_id"])
                    self._add_record(eid, ts, system, coll, r["resource_id"], r, src=f"bundle:{coll}")
                self.add_coverage(eid, ts, system, rids, f"bundle:{coll}")
        elif "service_exports" in d:
            self._load_service_export(eid, d)
        elif "records" in d and d["records"] and "record_type" in d["records"][0]:
            for r in d["records"]:
                self._add_record_lookup(eid, r.get("recorded_at") or cap, r, src="record_lookup_json")
        elif "collection" in d:
            coll = d["collection"]
            system = COLLECTION_SYSTEM.get(coll)
            rids = []
            for r in d["records"]:
                rids.append(r["resource_id"])
                self._add_record(eid, d.get("captured_at", cap), system, coll, r["resource_id"], r,
                                 src=f"collection:{coll}")
            self.add_coverage(eid, d.get("captured_at", cap), system, rids, f"collection:{coll}")
        else:
            raise ValueError(f"unknown json layout {path}")

    def _load_service_export(self, eid, d):
        ex = d["service_exports"]
        for system, colls in ex.items():
            for coll, items in colls.items():
                for it in items:
                    ts = it.get("observed_at")
                    rid = it["resource_id"]
                    if system == "account_service":
                        prof = it.get("profile")
                        if it.get("status_code") == 404 or prof is None:
                            self.add(eid, ts, system, rid, present=False, src="export:profile_lookup")
                        else:
                            self.add(eid, ts, system, rid, present=True,
                                     exposed=direct_identifiers(prof), keys=record_keys(prof),
                                     src="export:profile_lookup", extra={"data": prof})
                    elif system == "search_service":
                        docs = it.get("hits", {}).get("documents", [])
                        qk = norm_key(it.get("query"))
                        found = [doc for doc in docs if doc.get("resource_id") == rid]
                        if found:
                            doc = found[0]
                            self.add(eid, ts, system, rid, present=True, exposed=direct_identifiers(doc),
                                     keys=record_keys(doc) | {qk}, src="export:index_query",
                                     extra={"data": doc})
                        else:
                            self.add(eid, ts, system, rid, present=False, keys={qk},
                                     src="export:index_query")
                    else:
                        cat = {
                            "analytics_platform": "ANALYTICS_PROFILE",
                            "backup_service": "BACKUP_ARCHIVE",
                            "export_service": "USER_EXPORT",
                            "integration_service": "DATA_FLOW",
                            "security_service": "SECURITY_AUDIT_LOG",
                            "support_platform": "SUPPORT_MESSAGE",
                        }[system]
                        coll_name = {
                            "ANALYTICS_PROFILE": "customer_analytics_profiles",
                            "BACKUP_ARCHIVE": "backup_restore_entries",
                            "USER_EXPORT": "customer_exports",
                            "DATA_FLOW": "connector_subscriptions",
                            "SECURITY_AUDIT_LOG": "security_audit_records",
                            "SUPPORT_MESSAGE": "support_records",
                        }[cat]
                        self._add_record(eid, ts, system, coll_name, rid, it, src=f"export:{coll}")

    RESULT_STATE = {
        "FOUND": dict(present=True),
        "NOT_FOUND": dict(present=False),
        "IDENTIFIABLE": dict(present=True, exposed=True),
        "ANONYMIZED": dict(present=True, exposed=False),
        "REDACTED": dict(present=True, exposed=False),
        "ACTIVE": dict(present=True, active=True),
        "DISABLED": dict(present=True, active=False),
        "BLOCKED": dict(present=True, active=False),
    }

    def _add_record_lookup(self, eid, ts, r, src):
        state = dict(self.RESULT_STATE[r["result"]])
        key = r.get("subject_reference", r.get("lookup_value"))
        cat = r.get("record_type")
        if cat == "DATA_FLOW":
            cat = "DATA_FLOW"
        self.add(eid, ts, r["system"], r["resource_id"], keys={norm_key(key)}, cat=cat,
                 src=src, extra={"result": r["result"]}, **state)

    # TXT handover ---------------------------------------------------------
    def load_record_lines(self, eid, path):
        line_re = re.compile(r"^- (\S+) \| (\S+) \| (\S+) \| (\S+) \| (\S+) \| (.*)$")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = line_re.match(line.strip())
                if not m:
                    continue
                ts, system, rid, cat, result, subject = m.groups()
                self._add_record_lookup(eid, ts, {
                    "system": system, "resource_id": rid, "record_type": cat,
                    "result": result, "subject_reference": subject,
                }, src="handover")

    # LOGS -----------------------------------------------------------------
    LOOKUP_FIELDS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key",
                     "subject_ref", "correlation_key")

    def load_log(self, eid, path):
        events = logparse.parse_log(path)
        # record_lookup style logs
        if any(e["event"] == "record_lookup" for e in events):
            for e in events:
                if e["event"] != "record_lookup":
                    continue
                f = e["fields"]
                self._add_record_lookup(eid, e["ts"], {
                    "system": f["system"], "resource_id": f["resource_id"],
                    "record_type": f["record_type"], "result": f["result"],
                    "subject_reference": f.get("subject_reference"),
                }, src="record_lookup_log")
            return
        trace_keys = defaultdict(set)
        for e in events:
            for k in self.LOOKUP_FIELDS:
                v = e["fields"].get(k)
                if v and k != "correlation_key":
                    nk = norm_key(v)
                    if nk:
                        trace_keys[e["trace"]].add(nk)
        for e in events:
            f = e["fields"]
            rid = f.get("resource_id")
            if not rid:
                continue
            keys = set(trace_keys.get(e["trace"], set()))
            ck = norm_key(f.get("correlation_key"))
            if ck:
                keys.add(ck)
            for k in ("lookup_key", "subject_ref"):
                nk = norm_key(f.get(k))
                if nk:
                    keys.add(nk)
            keys.discard("rid:" + rid)
            ev = e["event"]
            ts = e["ts"]
            svc = e["service"]
            system = system_of(rid)
            src = f"log:{svc}:{ev}"
            if ev.endswith("_started") or ev in ("connection_acquired", "cache_lookup"):
                continue
            if ev == "profile_lookup_completed":
                rows = f.get("rows")
                status = f.get("http_status")
                present = not (rows == "0" or status == "404")
                data = {k: f.get(k) for k in ("customer_reference", "email", "phone") if k in f}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=present,
                         exposed=direct_identifiers(data) if present and data else None,
                         keys=keys, src=src)
            elif ev == "index_query_completed":
                present = f.get("result") == "hit"
                data = {k: f.get(k) for k in ("customer_reference", "email") if k in f}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=present, keys=keys, src=src)
            elif ev == "message_pii_scan":
                keys |= {norm_key(f.get("matched_value"))}
                self.add(eid, ts, system, rid, present=True, exposed=True, keys=keys,
                         cat="SUPPORT_MESSAGE", src=src)
            elif ev == "message_read":
                txt = f.get("message_text", "")
                exposed = "[REDACTED]" not in txt and bool(EMAIL_RE.search(txt))
                keys |= keys_from_text(txt)
                self.add(eid, ts, system, rid, present=True, exposed=exposed, keys=keys,
                         cat="SUPPORT_MESSAGE", src=src)
            elif ev == "attachment_pii_scan":
                keys |= {norm_key(f.get("matched_value"))}
                self.add(eid, ts, system, rid, present=True, exposed=True, keys=keys,
                         cat="SUPPORT_ATTACHMENT", src=src, extra={"file": f.get("file")})
            elif ev == "support_ticket_read_completed":
                data = {k: f.get(k) for k in ("customer_reference", "requester_name",
                                               "requester_email", "requester_phone")}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=True, exposed=direct_identifiers(data),
                         keys=keys, cat="SUPPORT_MESSAGE", src=src)
            elif ev == "analytics_identity_scan":
                keys |= {norm_key(f.get("matched_value"))}
                self.add(eid, ts, system, rid, present=True, exposed=True, keys=keys, src=src)
            elif ev in ("analytics_profile_read", "analytics_profile_read_completed"):
                data = {k: f.get(k) for k in ("customer_reference", "email", "phone", "email_hash")
                        if k in f}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                exposed = direct_identifiers(data) or f.get("direct_identifiers") not in (None, "0")
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=True, exposed=exposed, keys=keys, src=src)
            elif ev == "contact_lookup_completed":
                self.add(eid, ts, system, rid, present=f.get("rows") != "0", exposed=True, keys=keys,
                         cat="SECONDARY_IDENTIFIER", src=src)
            elif ev in ("connector_heartbeat", "connector_config_loaded"):
                active = f.get("enabled") == "true"
                self.add(eid, ts, system, rid, present=True, active=active, keys=keys, src=src)
            elif ev == "connector_config_read_completed":
                data = {k: f.get(k) for k in ("customer_email", "customer_reference")}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=True, active=f.get("enabled") == "true",
                         keys=keys, src=src)
            elif ev == "restore_candidate_evaluated":
                active = f.get("eligible") == "true" and f.get("deletion_block") != "true"
                self.add(eid, ts, system, rid, present=True, active=active, keys=keys, src=src)
            elif ev == "restore_authorization_completed":
                self.add(eid, ts, system, rid, present=True, active=f.get("decision") != "deny",
                         keys=keys, src=src)
            elif ev == "restore_policy_read_completed":
                data = {k: f.get(k) for k in ("customer_email", "customer_reference")}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=f.get("archive_present") != "false",
                         active=f.get("restore_allowed") == "true", keys=keys, src=src)
            elif ev == "export_download_check_completed":
                self.add(eid, ts, system, rid, present=f.get("available") == "true", keys=keys,
                         src=src, extra={"file": f.get("file")})
            elif ev == "export_metadata_read_completed":
                data = {"owner_email": f.get("owner_email")}
                data = {k: (None if v in ("null", None) else v) for k, v in data.items()}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=f.get("available") == "true", keys=keys,
                         src=src)
            elif ev == "audit_record_read_completed":
                data = {"subject_reference": f.get("subject_reference")}
                keys |= record_keys(data)
                self.add(eid, ts, system, rid, present=f.get("rows", "1") != "0", keys=keys, src=src)
            elif ev == "retained_order_loaded":
                self.add(eid, ts, "legal_service", rid, present=True, keys=keys, src=src,
                         cat="LEGAL_HOLD_RECORD")
            elif ev == "contact_query_completed":
                self.add(eid, ts, system, rid, present=f.get("rows") != "0", keys=keys, src=src)
            elif ev == "deletion_marker_check":
                self.add(eid, ts, system, rid, present=f.get("marker_found") == "false", keys=keys,
                         src=src)
            elif ev == "thumbnail_failed":
                continue
            else:
                raise ValueError(f"unhandled log event {ev} in {path}")

    # Images / PDFs ----------------------------------------------------------
    def load_transcriptions(self, path):
        """Facts read from screenshots/PDFs (see evidence_transcriptions.json)."""
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data["observations"]:
            keys = {norm_key(k) for k in item.get("keys", [])}
            self.add(item["eid"], item["ts"], item["system"], item["rid"],
                     present=item.get("present"), exposed=item.get("exposed"),
                     active=item.get("active"), keys=keys, cat=item.get("cat"),
                     src="transcription:" + item.get("note", ""),
                     extra={"featured": item.get("featured", True)})


def load(dataset_dir: str) -> Store:
    return Store(dataset_dir).load_all()
