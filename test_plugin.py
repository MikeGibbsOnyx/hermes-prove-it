"""End-to-end through the real hooks, on a throwaway HERMES_HOME (never the live profile).
Run: ~/.hermes/hermes-agent/venv/bin/python test_plugin.py"""
import importlib, os, subprocess, sys, tempfile
home = tempfile.mkdtemp(); os.environ["HERMES_HOME"] = home
os.makedirs(f"{home}/pk"); os.symlink(os.path.dirname(os.path.abspath(__file__)), f"{home}/pk/prove_it")
sys.path[:0] = [f"{home}/pk", os.path.expanduser("~/.hermes/hermes-agent")]
p = importlib.import_module("prove_it")

class Ctx:
    def __init__(s): s.h, s.c = {}, {}
    def register_hook(s, n, f, **k): s.h[n] = f
    def register_command(s, n, f, **k): s.c[n] = f
ctx = Ctx(); p.register(ctx)
assert set(ctx.h) == {"post_tool_call", "pre_approval_request", "post_approval_response", "transform_llm_output"}

# a turn blocked on approval that then replies with no claims: the footer still says so
ctx.h["pre_approval_request"](turn_id="T1", tool_call_id="c1", pattern_key="rm -rf", description="delete the build dir", command="rm -rf /secret")
ctx.h["post_approval_response"](turn_id="T1", tool_call_id="c1", choice="deny")
out = ctx.h["transform_llm_output"](response_text="I could not clear the folder.", session_id="S", turn_id="T1")
assert out and "\u23f8 blocked" in out and "deny" in out and "secret" not in out, out

# approved prompt leaves no footer; a reply with no claims is untouched
ctx.h["pre_approval_request"](turn_id="T2", tool_call_id="c2", pattern_key="x", description="ok")
ctx.h["post_approval_response"](turn_id="T2", tool_call_id="c2", choice="once")
assert ctx.h["transform_llm_output"](response_text="All done here.", session_id="S", turn_id="T2") is None

# a claim with nothing behind it, then /proveit explain shows why
out = ctx.h["transform_llm_output"](response_text="Done. I wrote `/definitely/not/here.txt` for you.", session_id="S", turn_id="T3")
assert "\u2717 not written" in out, out
detail = ctx.c["proveit"]("explain")
assert "probe: stat /definitely/not/here.txt" in detail and "ledger:" in detail, detail
# the ledger must not keep secrets
ctx.h["post_tool_call"](tool_name="terminal", args={"command": "curl -H 'Authorization: Bearer abcdef1234567890abcdef' https://x; export API_KEY=supersecretvalue99"}, result="ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok", session_id="S", turn_id="T4", status="ok")
row = p._db().execute("SELECT args, result FROM ledger WHERE turn_id='T4'").fetchone()
assert "abcdef1234567890abcdef" not in row[0] and "supersecretvalue99" not in row[0] and "ghp_abcdef" not in row[1], row
ctx.h["post_tool_call"](tool_name="terminal", args={"command": "curl -d '{\\\"token\\\":\\\"abcdefgh12345678\\\"}' https://u:hunter2pw@host/x; TOKEN=123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"}, result="x" * 5000 + ' "exit_code": 7', session_id="S", turn_id="T5", status="ok")
ctx.h["post_tool_call"](tool_name="read_file", args={"path": "/etc/secret"}, result="private file contents", session_id="S", turn_id="T5", status="ok")
rows = p._db().execute("SELECT tool_name, args, result FROM ledger WHERE turn_id='T5' ORDER BY id").fetchall()
assert "abcdefgh12345678" not in rows[0][1] and "hunter2pw" not in rows[0][1] and "AAHdqTcv" not in rows[0][1], rows[0][1]
assert rows[0][2].startswith("[exit=7] "), rows[0][2][:20]          # exit code survives truncation
assert rows[1][1] == "" and rows[1][2] == ""                         # unused tools keep no arguments or output
# a huge output must not stall the observer hook (the secret pattern is quadratic on big input)
import time as _t; _s = _t.time()
ctx.h["post_tool_call"](tool_name="terminal", args={"command": "x" * 400000}, result="a:b" * 150000 + ' "exit_code": 0', session_id="S", turn_id="T6", status="ok")
assert _t.time() - _s < 0.5, _t.time() - _s
print(out.split("\n\n")[1]); print("plugin: all checks pass")
