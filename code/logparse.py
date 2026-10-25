"""Parsers for the observability log formats found under dataset/evidence/logs.

Every supported line format is normalised to a dict:
    {"ts", "level", "service", "trace", "event", "fields": {...}, "line_no"}
"""
from __future__ import annotations

import json
import re

KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')
TS_RE = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)"

SYSLOG_RE = re.compile(TS_RE + r" <\d+>\d+ (\S+) (\S+) (\S+) - - (.*)$")
BRACKET_RE = re.compile(TS_RE + r" \[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] ([A-Za-z_]+): ?(.*)$")
PIPE_RE = re.compile(TS_RE + r" \| (\w+) \| (\S+) \| (\S+) \| (\S+) \| ?(.*)$")
JSON_RE = re.compile(TS_RE + r" (\{.*\})\s*$")
CLOUD_RE = re.compile(TS_RE + r" requestId=(\S+) level=(\w+) function=(\S+) message=\"(.*)\"\s*$")
LOGFMT_RE = re.compile(TS_RE + r" (?:(INFO|DEBUG|WARN|ERROR) )?(.*)$")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
        v = v.replace('\\"', '"').replace("\\\\", "\\")
    return v


def parse_kv(text: str) -> dict:
    return {k: _unquote(v) for k, v in KV_RE.findall(text)}


def parse_line(line: str) -> dict | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    m = JSON_RE.match(line)
    if m:
        d = json.loads(m.group(2))
        fields = {k: str(v) for k, v in d.items()}
        return {
            "ts": m.group(1),
            "level": fields.pop("severity", ""),
            "service": fields.pop("service", fields.get("logger", "")),
            "trace": fields.pop("trace_id", ""),
            "event": fields.pop("event", ""),
            "fields": fields,
        }
    m = CLOUD_RE.match(line)
    if m:
        msg = m.group(5).replace('\\"', '"')
        fields = parse_kv(msg)
        return {
            "ts": m.group(1),
            "level": m.group(3),
            "service": m.group(4),
            "trace": m.group(2),
            "event": fields.pop("event", ""),
            "fields": fields,
        }
    m = SYSLOG_RE.match(line)
    if m:
        fields = parse_kv(m.group(5))
        return {
            "ts": m.group(1),
            "level": fields.pop("level", ""),
            "service": m.group(3),
            "trace": m.group(4),
            "event": fields.pop("event", ""),
            "fields": fields,
        }
    m = BRACKET_RE.match(line)
    if m:
        fields = parse_kv(m.group(6))
        return {
            "ts": m.group(1),
            "level": m.group(2),
            "service": m.group(3),
            "trace": m.group(4),
            "event": m.group(5),
            "fields": fields,
        }
    m = PIPE_RE.match(line)
    if m:
        fields = parse_kv(m.group(6))
        return {
            "ts": m.group(1),
            "level": m.group(2),
            "service": m.group(3),
            "trace": m.group(4),
            "event": m.group(5),
            "fields": fields,
        }
    m = LOGFMT_RE.match(line)
    if m:
        fields = parse_kv(m.group(3))
        level = fields.pop("level", m.group(2) or "")
        return {
            "ts": m.group(1),
            "level": level,
            "service": fields.pop("service", ""),
            "trace": fields.pop("trace_id", ""),
            "event": fields.pop("event", ""),
            "fields": fields,
        }
    return None


def parse_log(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            rec = parse_line(line)
            if rec is None:
                if line.strip():
                    raise ValueError(f"unparsed log line {path}:{i}: {line[:120]}")
                continue
            rec["line_no"] = i
            out.append(rec)
    return out
