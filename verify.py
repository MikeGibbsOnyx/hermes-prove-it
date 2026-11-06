"""Probes and footer. Every probe is local and read-only (git rev-parse / merge-base / cat-file / log, os.stat):
none writes, fetches, contacts a remote or authenticates."""
from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, wait

try:
    from .claims import extract, judge
except ImportError:  # run as a plain script / tests
    from claims import extract, judge

PROBE_TIMEOUT = 2.0   # seconds per subprocess
BUDGET = 4.0          # seconds for the whole footer
MAX_LINES = 5
_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never block on a credential prompt


def _git(repo, *args):
    r = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "protocol.ext.allow=never", "-C", repo, *args], capture_output=True, text=True, timeout=PROBE_TIMEOUT, env=_ENV, stdin=subprocess.DEVNULL,
                       start_new_session=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _abs_dir(d):
    d = os.path.expanduser((d or "").strip("\"'"))
    return d if os.path.isabs(d) and os.path.isdir(d) else None  # a relative path has no cwd to resolve against


def repo_hint(calls):
    """Directory the last git push/commit ran in: `git -C X`, the last `cd X` before it, or a workdir arg. Absolute only."""
    for c in reversed(calls):
        a = c.get("args") or {}
        cmd = a.get("command", "") if isinstance(a, dict) else ""
        m = re.search(r"\bgit\b[^\n|;&]*\b(?:push|commit)\b", cmd)
        if not m:
            continue
        head = cmd[:m.start()]
        dm = re.search(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)", m.group(0))
        cds = re.findall(r"\bcd\s+(\"[^\"]+\"|'[^']+'|[^\s&;]+)", head)
        d = _abs_dir(dm.group(1)) if dm else (_abs_dir(cds[-1]) if cds else None)
        d = d or _abs_dir(a.get("workdir") if isinstance(a, dict) else None)
        if d:
            return d
    return None


def _push_target(calls):
    """(remote, refspec) named by the last `git push` this turn, else (None, None)."""
    for c in reversed(calls):
        a = c.get("args") or {}
        cmd = a.get("command", "") if isinstance(a, dict) else ""
        m = re.search(r"\bgit\b[^\n|;&]*?\bpush\b([^\n|;&]*)", cmd)
        if m:
            args = [t for t in m.group(1).split() if not t.startswith("-")]
            return (args[0] if args else None), (args[1] if len(args) > 1 else None)
    return None, None


def probe_pushed(repo, calls=()):
    """Local HEAD vs the remote-tracking ref of its upstream. A push that lands updates that ref; a rejected one does not."""
    if not repo or not os.path.isdir(repo):
        return "unknown", "no repository found for this turn", "-", ""
    rc, head, _ = _git(repo, "rev-parse", "HEAD")
    rc2, branch, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc or rc2:
        return "unknown", "not a git repository", "git rev-parse HEAD", ""
    if branch == "HEAD":
        return "unknown", "detached HEAD: no branch to compare", "git rev-parse --abbrev-ref HEAD", branch
    rc, up, _ = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc or "/" not in up:
        return "unknown", f"{branch} has no upstream to compare", "git rev-parse @{u}", ""
    remote = up.split("/", 1)[0]
    pr, ps = _push_target(calls)
    if (pr and pr != remote) or (ps and (":" in ps or ps not in ("HEAD", branch))):
        return "unknown", f"the push named {pr or ''} {ps or ''}; only {up} is checked".replace("  ", " "), "-", ""
    rc, usha, _ = _git(repo, "rev-parse", "@{u}")
    cmd = "git rev-parse HEAD @{u}"
    raw = f"HEAD={head[:12]} {up}={usha[:12]}"
    if rc:
        return "unknown", "could not read the tracking ref", cmd, raw
    if usha == head or _git(repo, "merge-base", "--is-ancestor", head, usha)[0] == 0:
        return "ok", f"{up} \u2192 {usha[:7]} contains {head[:7]}", cmd, raw
    if _git(repo, "merge-base", "--is-ancestor", usha, head)[0] == 0:
        return "bad", f"{up} is at {usha[:7]}, behind local {head[:7]}", cmd, raw
    return "unknown", f"{up} {usha[:7]} vs local {head[:7]}: diverged", cmd, raw


def reply_repo(reply):
    """A git work tree named by path in the reply itself ("pushed /srv/app to origin")."""
    for m in re.finditer(r"(?:~|/)[\w.~/@+-]{2,}", reply or ""):
        p = os.path.expanduser(m.group(0)).rstrip(".,;:)")
        d = p if os.path.isdir(p) else os.path.dirname(p)
        if os.path.isdir(d):
            try:
                rc, top, _ = _git(d, "rev-parse", "--show-toplevel")
            except Exception:
                continue
            if rc == 0 and top:
                return top
    return None


def cwd_repo():
    """The git work tree Hermes was started in, if any: the fallback when this turn ran no git command."""
    try:
        rc, top, _ = _git(os.getcwd(), "rev-parse", "--show-toplevel")
        return top if rc == 0 and top else None
    except Exception:
        return None


def probe_committed(repo, calls):
    h = None
    for c in reversed(calls):
        m = re.search(r"\[[\w./-]+(?:\s+\(root-commit\))?\s+([0-9a-f]{7,})\]", str(c.get("result", "")))
        if m:
            h = m.group(1)
            break
    made_one = any(re.search(r"\bgit\b[^\n|;&]*\bcommit\b", (c.get("args") or {}).get("command", "") if isinstance(c.get("args"), dict) else "") for c in calls)
    if not h and made_one and repo and os.path.isdir(repo):  # this turn ran git commit but printed no hash: a recent HEAD, said so
        rc, out, _ = _git(repo, "log", "-1", "--format=%h %ct")
        if rc == 0 and out:
            sha, ct = out.split()
            age = int(time.time()) - int(ct)
            if age <= 1800:
                return "ok", f"{sha} (HEAD, {max(age // 60, 0)} min ago)", "git log -1 --format=%h %ct", out
    if not h or not repo or not os.path.isdir(repo):
        return "unknown", "no commit hash in this turn's output", "-", ""
    rc, out, err = _git(repo, "cat-file", "-t", h)
    return ("ok", h[:7], f"git cat-file -t {h[:7]}", out) if rc == 0 and out == "commit" else \
           ("unknown", f"{h[:7]} not found in {os.path.basename(repo)} (another repo?)", f"git cat-file -t {h[:7]}", err)


def probe_wrote(path, turn_start=0.0):
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        return "unknown", f"{path} is relative; no working directory to resolve it", "-", ""
    try:
        st = os.stat(p)
    except OSError as e:
        return "bad", path, f"stat {path}", str(e)
    raw = f"size={st.st_size} mtime={int(st.st_mtime)}"
    if turn_start and st.st_mtime < turn_start - 5:  # existed before this turn: not evidence of a write
        return "unknown", f"{path} exists but was not modified this turn", f"stat {path}", raw
    return "ok", f"{path} ({st.st_size:,} B)", f"stat {path}", raw


def _local_backend():
    return os.environ.get("TERMINAL_ENV", "local") in ("", "local")  # docker/ssh/modal: files live elsewhere


def _one(claim, calls, history, repo):
    t0 = time.time()
    turn_start = min([c["ts"] for c in calls if c.get("ts")] or [0.0])
    sv, sreason = judge(claim, calls, exists=os.path.exists if _local_backend() else (lambda p: True), history=history)
    if sv == "skip":
        return None
    typ, pv, pline, pcmd, praw = claim["type"], None, "", "", ""
    try:
        if not _local_backend():
            pv, pline, pcmd, praw = "unknown", "terminal backend is not local; host probes would look at the wrong machine", "-", ""
        elif typ == "pushed":
            pv, pline, pcmd, praw = probe_pushed(repo, calls)
        elif typ == "committed":
            pv, pline, pcmd, praw = probe_committed(repo, calls)
        elif typ == "wrote":
            pv, pline, pcmd, praw = probe_wrote(claim["path"], turn_start)
    except Exception as e:  # a broken probe is "unknown", never a verdict
        pv, pline, pcmd, praw = "unknown", f"probe error: {type(e).__name__}", "-", str(e)
    if pv in ("ok", "bad"):                      # a probe that decided is authoritative
        verdict, why = pv, pline
    elif pv == "unknown" and sv == "bad":        # the probe could not decide; a recorded failure still stands
        verdict, why = "bad", sreason
    elif pv == "unknown":                        # a probe ran and could not decide: static ok is not enough
        verdict, why = "unknown", (sreason if sv == "unknown" else pline)
    else:                                        # no probe for this claim type (tests)
        verdict, why = sv, sreason
    return {"type": typ, "verdict": verdict, "why": why, "sentence": claim["sentence"], "probe": pcmd,
            "raw": praw[:300], "ms": int((time.time() - t0) * 1000), "static": sv, "static_why": sreason}


def line(r):
    t, v, why, s = r["type"], r["verdict"], r["why"], r["sentence"]
    if v == "ok":
        return f"\u2713 {t:<9} {why}"
    if v == "bad":
        name = {"pushed": "not pushed", "committed": "not committed", "wrote": "not written", "tests": "tests"}[t]
        return f"\u2717 {name:<9} {why}"
    return f"\u26a0 unverified  \"{s[:50]}\" \u2014 {why}"


def _cmdtext(c):
    a = c.get("args") or {}
    return a.get("command", "") if isinstance(a, dict) else ""


def check(reply, calls, history=()):
    """-> list of result dicts (empty = nothing to say). Bounded by BUDGET; never raises past the caller."""
    claims, seen = [], set()
    git_hint = any(re.search(r"\bgit\b[^\n|;&]*\b(?:push|commit)\b", _cmdtext(c)) for c in calls)
    for c in extract(reply, git_hint):
        key = (c["type"], c.get("path"))
        if key not in seen:
            seen.add(key)
            claims.append(c)
    if not claims:
        return []
    repo = repo_hint(calls) or repo_hint(history) or reply_repo(reply) or cwd_repo()
    ex = ThreadPoolExecutor(max_workers=len(claims))
    futs = {ex.submit(_one, c, list(calls), list(history), repo): c for c in claims}
    done, pending = wait(futs, timeout=BUDGET)
    out = []
    for f, c in futs.items():
        if f in done:
            try:
                r = f.result()
            except Exception as e:
                r = {"type": c["type"], "verdict": "unknown", "why": f"error: {type(e).__name__}", "sentence": c["sentence"],
                     "probe": "-", "raw": "", "ms": 0, "static": "unknown", "static_why": ""}
        else:
            r = {"type": c["type"], "verdict": "unknown", "why": "timeout", "sentence": c["sentence"],
                 "probe": "-", "raw": "", "ms": int(BUDGET * 1000), "static": "unknown", "static_why": ""}
        if r:
            out.append(r)
    ex.shutdown(wait=False, cancel_futures=True)
    order = {"bad": 0, "unknown": 1, "ok": 2}
    return sorted(out, key=lambda r: order[r["verdict"]])


def footer(results):
    lines = [line(r) for r in results]
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES - 1] + [f"+{len(lines) - MAX_LINES + 1} more \u2014 /proveit"]
    return "\n".join(lines)
