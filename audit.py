"""Dry-run audit over past sessions: python3 audit.py [state.db] [max_sessions]
Static judge only (no live probes, parent sessions followed): it measures claim extraction and the ledger rules, not the shipped probes.
Prints claim counts and every 'bad' verdict so a human can hand-label false flags. Changes nothing."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter

from claims import extract, judge


def _turns(db):
    cur = db.execute("SELECT session_id, role, content, tool_calls, tool_call_id, tool_name, display_kind FROM messages "
                     "WHERE active=1 AND role IN ('user','assistant','tool') ORDER BY session_id, id")
    parent = dict(db.execute("SELECT id, parent_session_id FROM sessions WHERE parent_session_id IS NOT NULL"))
    hist = {}  # session -> all calls from earlier turns (plus parent chain)
    tail = {}  # session -> calls of its last turn, so a compaction mid-turn keeps the earlier tool calls
    sid, calls, by_id, final = None, [], {}, None
    for s, role, content, tcs, tcid, tname, kind in cur:
        if role == "user" and kind in ("steer", "internal_notification"):
            continue  # mid-turn steering is not a new turn
        if s != sid or role == "user":
            if final is not None:
                yield sid, final, calls, hist.get(sid, [])
            if sid is not None:
                hist.setdefault(sid, []).extend(calls)
            if s != sid and s in parent:
                hist[s] = list(hist.get(parent[s], []))
            if sid is not None and s != sid:
                tail[sid] = calls
            carry = s != sid and s in parent and role == "user" and (content or "").startswith("[CONTEXT COMPACTION")
            if carry:
                calls = list(tail.get(parent[s], []))
            elif s != sid or role == "user":
                calls = []
            sid, by_id, final = s, {}, None
            if role == "user":
                continue
        if role == "assistant":
            if tcs:
                try:
                    for tc in json.loads(tcs):
                        f = tc.get("function", {})
                        try:
                            args = json.loads(f.get("arguments") or "{}")
                        except Exception:
                            args = {"raw": f.get("arguments")}
                        c = {"name": f.get("name"), "args": args, "result": "", "exit": None}
                        calls.append(c)
                        by_id[tc.get("id")] = c
                except Exception:
                    pass
            elif content and len(content) > 20:
                final = content
        elif role == "tool" and tcid in by_id:
            c = by_id[tcid]
            c["result"] = content or ""
            try:
                d = json.loads(content)
                if isinstance(d, dict) and "exit_code" in d:
                    c["exit"] = d["exit_code"]
            except Exception:
                pass
    if final is not None:
        yield sid, final, calls, hist.get(sid, [])


def main(path, max_sessions=None):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    stats, bad, replies, with_claims = Counter(), [], 0, 0
    seen = set()
    for sid, reply, calls, history in _turns(db):
        seen.add(sid)
        if max_sessions and len(seen) > max_sessions:
            break
        replies += 1
        cl = extract(reply, any(re.search(r'\bgit\b[^\n|;&]*\b(?:push|commit)\b', str((c.get('args') or {}).get('command', '') if isinstance(c.get('args'), dict) else '')) for c in calls))
        if cl:
            with_claims += 1
        for c in cl:
            v, why = judge(c, calls, history=history, exists=lambda p: os.path.exists(p) or p.startswith(("/tmp", os.path.expanduser("~/.hermes/cache/scratch"))))
            stats[(c["type"], v)] += 1
            if v == "bad":
                bad.append((sid[-8:], c["type"], c["sentence"][:140], why))
    print(f"final replies: {replies}  with a claim: {with_claims}")
    for (t, v), n in sorted(stats.items()):
        print(f"  {t:10s} {v:8s} {n}")
    print(f"\n{len(bad)} 'bad' verdicts to hand-check:")
    for b in bad[:int(os.environ.get('SHOW', 60))]:
        print(" ", *b, sep=" | ")


if __name__ == "__main__":
    a = sys.argv[1:]
    p = a[0] if a and a[0].endswith(".db") else os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "state.db")
    n = next((int(x) for x in a if x.isdigit()), None)
    main(p, n)
