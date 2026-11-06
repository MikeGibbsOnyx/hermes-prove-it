"""prove-it: checks an agent's completion claims against read-only probes. No model in the path.
Mode (env PROVE_IT_MODE, read at load): footer (default) | strict (hold the reply until checked) | off (ledger only)."""
from __future__ import annotations

import json
import os
from contextlib import closing
import re
import time

from plugins.plugin_storage import plugin_db

from . import claims, verify

NAME = "prove-it"
MODE = os.environ.get("PROVE_IT_MODE", "footer").lower()  # ponytail: env var; move to plugin config if users ask
_CAP = 4000
_KEEP_DAYS = 7  # ponytail: fixed retention; make it a setting if anyone asks
_SECRET = re.compile(
    r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|(://)[^/\s:@]+:[^@\s]+@"
    r"|((?:api[_-]?key|token|secret|passw(?:or)?d|authorization|bearer)[\"']?\s*[:=]?\s*[\"']?)[A-Za-z0-9._~+/=:\-]{8,}"
    r"|\b(?:sk|ghp|gho|ghs|glpat|github_pat|xox[a-z]|xapp|hf)[-_][A-Za-z0-9_\-]{16,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")
_pruned = False
# only what judge reads is kept; every other tool is recorded as "a call happened" with no arguments or output
_KEEP_TOOLS = {"terminal", "process_manage", "process", "write_file", "patch", "edit_file", "create_file",
               "execute_code", "delegate_task", "mixture_of_agents"}


def _redact(text):
    text = str(text).replace('\\"', '"')  # escaped JSON quotes would hide the key from the pattern
    return _SECRET.sub(lambda m: (m.group(1) + "[redacted]@") if m.group(1) else
                       ((m.group(2) + "[redacted]") if m.group(2) else "[redacted]"), text)


def _walk(o):
    if isinstance(o, str):
        return _redact(o)
    if isinstance(o, dict):
        return {k: _walk(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_walk(v) for v in o]
    return o


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY, ts REAL, turn_id TEXT, session_id TEXT, tool_call_id TEXT,
  tool_name TEXT, status TEXT, error_type TEXT, args TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS ledger_turn ON ledger(turn_id);
CREATE INDEX IF NOT EXISTS ledger_session ON ledger(session_id);
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, ts REAL, turn_id TEXT, session_id TEXT, type TEXT, verdict TEXT,
  line TEXT, claim TEXT, probe TEXT, raw TEXT, ms INTEGER, why TEXT);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY, ts REAL, turn_id TEXT, tool_call_id TEXT, pattern TEXT, description TEXT, choice TEXT);
CREATE INDEX IF NOT EXISTS approvals_turn ON approvals(turn_id);
"""


def _db():
    conn = plugin_db(NAME)
    conn.executescript(_SCHEMA)
    global _pruned
    if not _pruned:  # once per process; commit at once so no write lock is left open
        cut = time.time() - _KEEP_DAYS * 86400
        for t in ("ledger", "receipts", "approvals"):
            conn.execute(f"DELETE FROM {t} WHERE ts<?", (cut,))
        conn.commit()
        _pruned = True
    return conn


def _on_post_tool_call(tool_name="", args=None, result="", session_id="", turn_id="",
                       tool_call_id="", status="", error_type="", **kwargs):
    try:  # observer hook: never raise, never touch the turn
        keep = tool_name in _KEEP_TOOLS
        text = str(result)
        m = re.search(r"[\"']exit_code[\"']:\s*(-?\d+)", text)  # read before truncation: it sits after the output
        stored = (f"[exit={m.group(1)}] " if m else "") + _redact(text)[:_CAP] if keep else ""
        with closing(_db()) as conn:
            conn.execute(
                "INSERT INTO ledger(ts,turn_id,session_id,tool_call_id,tool_name,status,error_type,args,result)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), turn_id or "", session_id or "", tool_call_id or "", tool_name,
                 status or "", error_type or "", json.dumps(_walk(args), default=str)[:_CAP] if keep else "", stored))
            conn.commit()
    except Exception:
        pass


def _on_approval_request(turn_id="", tool_call_id="", pattern_key="", description="", **kwargs):
    try:  # the command text can hold secrets: store only the pattern and the description
        with closing(_db()) as conn:
            conn.execute("INSERT INTO approvals(ts,turn_id,tool_call_id,pattern,description) VALUES (?,?,?,?,?)",
                         (time.time(), turn_id or "", tool_call_id or "", str(pattern_key)[:80], str(description)[:120]))
            conn.commit()
    except Exception:
        pass


def _on_approval_response(turn_id="", tool_call_id="", choice="", **kwargs):
    try:
        with closing(_db()) as conn:  # one row: the oldest still-pending prompt for this call (or this turn)
            conn.execute("UPDATE approvals SET choice=? WHERE id=(SELECT id FROM approvals WHERE turn_id=? AND"
                         " (tool_call_id=? OR ?='') AND choice IS NULL ORDER BY id LIMIT 1)",
                         (str(choice or "none"), turn_id or "", tool_call_id or "", tool_call_id or ""))
            conn.commit()
    except Exception:
        pass


def _blocked(conn, turn_id):
    """Approval prompts this turn that were not granted: still pending, denied or timed out."""
    out = []
    for desc, pat, choice in conn.execute("SELECT description,pattern,choice FROM approvals WHERE turn_id=? ORDER BY id", (turn_id,)):
        if choice is None or choice.lower().startswith(("deny", "timeout", "none", "reject")):
            out.append(f"\u23f8 blocked    {(desc or pat or 'a command')[:50]} \u2014 approval {choice or 'pending'}")
    return out


def _calls(conn, where, params):
    out = []
    for name, args, result, ts in conn.execute(f"SELECT tool_name,args,result,ts FROM ledger WHERE {where} ORDER BY id", params):
        try:
            a = json.loads(args)
        except Exception:  # truncated JSON: recover the command text so probes still see it
            m = re.search(r'"command":\s*"(.*)', args or "")
            a = {"command": m.group(1).replace('\\"', '"')} if m else {"raw": args}
        m = re.match(r"\[exit=(-?\d+)\] ", result or "")
        out.append({"name": name, "args": a, "result": result or "", "exit": int(m.group(1)) if m else None, "ts": ts})
    return out


def _transform(response_text="", session_id="", turn_id="", **kwargs):
    reply = response_text
    if MODE == "off":
        return None
    try:
        with closing(_db()) as conn:
            calls = _calls(conn, "turn_id=?", (turn_id,))
            history = _calls(conn, "session_id=? AND turn_id!=?", (session_id, turn_id))
            results = verify.check(reply, calls, history)
            blocked = _blocked(conn, turn_id)
            if not results and not blocked:
                return None  # nothing to say: leave the reply exactly as it was
            now = time.time()
            for r in results:
                conn.execute("INSERT INTO receipts(ts,turn_id,session_id,type,verdict,line,claim,probe,raw,ms,why) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (now, turn_id, session_id, r["type"], r["verdict"], verify.line(r), r["sentence"], r["probe"], r["raw"], r["ms"], r["static_why"]))
            conn.commit()
        lines = blocked + ([verify.footer(results)] if results else [])
        return reply + "\n\n" + "\n".join(lines)
    except Exception as e:  # degrade the footer, never the reply
        try:
            if not claims.extract(reply or ""):
                return None  # nothing was claimed: do not announce a failure about nothing
        except Exception:
            pass
        return (reply or "") + f"\n\n\u26a0 prove-it: verification unavailable ({type(e).__name__})"


def _slash(raw_args: str = "") -> str:
    try:
        with closing(_db()) as conn:
            n, turns = conn.execute("SELECT COUNT(*), COUNT(DISTINCT turn_id) FROM ledger").fetchone()
            last = conn.execute("SELECT turn_id FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
            head = f"prove-it ledger: {n} tool calls, {turns} turns, mode={MODE}."
            if not last:
                return head + " No claims checked yet."
            rows = conn.execute("SELECT line,claim,probe,raw,ms,why FROM receipts WHERE turn_id=? ORDER BY id", (last[0],)).fetchall()
            full = raw_args.strip().lower() == "explain"
            body = [f"last checked turn {last[0][:8]}:"]
            for line, claim, probe, raw, ms, why in rows:
                body += [f"  {line}", f"      claim: \"{claim[:200 if full else 80]}\"", f"      probe: {probe}  [{ms} ms]"]
                if raw:
                    body.append(f"      raw:   {raw[:300 if full else 120]}")
                if full and why:
                    body.append(f"      ledger: {why}")
            for line in _blocked(conn, last[0]):
                body.append("  " + line)
            return head + "\n" + "\n".join(body)
    except Exception as e:
        return f"prove-it: unavailable ({e})"


def register(ctx):
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_approval_request", _on_approval_request)
    ctx.register_hook("post_approval_response", _on_approval_response)
    ctx.register_hook("transform_llm_output", _transform, hold_stream=(MODE == "strict"))  # MODE=off returns None inside
    ctx.register_command("proveit", _slash, description="Show what prove-it checked and why (/proveit explain for full detail)")
