"""One runnable check against real temp git repos: python3 test_verify.py"""
import os, subprocess, tempfile, time
import verify

def sh(*a, cwd=None):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

tmp = tempfile.mkdtemp()
remote, repo = f"{tmp}/remote.git", f"{tmp}/repo"
sh("git", "init", "-q", "--bare", remote)
sh("git", "clone", "-q", remote, repo)
open(f"{repo}/a.txt", "w").write("x")
sh("git", "add", "-A", cwd=repo); sh("git", "commit", "-q", "-m", "one", cwd=repo)
sh("git", "branch", "-M", "main", cwd=repo); sh("git", "push", "-q", "-u", "origin", "main", cwd=repo)
head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
commit_call = lambda: {"name": "terminal", "args": {"command": f"cd {repo} && git commit -m x"}, "result": f"[main {head[:7]}] x\n 1 file changed", "exit": 0}
push_call = {"name": "terminal", "args": {"command": f"cd {repo} && git push"}, "result": "main -> main", "exit": 0}

# A: commit + push really happened
r = {x["type"]: x for x in verify.check("Committed and pushed to the repo.", [commit_call(), push_call])}
assert r["pushed"]["verdict"] == "ok" and r["committed"]["verdict"] == "ok", r

# B/C: a second commit that was never pushed; claim says pushed -> committed green, pushed red (the split claim)
open(f"{repo}/b.txt", "w").write("y"); sh("git", "add", "-A", cwd=repo); sh("git", "commit", "-q", "-m", "two", cwd=repo)
head2 = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
c2 = {"name": "terminal", "args": {"command": f"cd {repo} && git commit -m two"}, "result": f"[main {head2[:7]}] two\n 1 file changed", "exit": 0}
r = {x["type"]: x for x in verify.check("Committed and pushed to the repo.", [c2])}
assert r["committed"]["verdict"] == "ok" and r["pushed"]["verdict"] == "bad", r
print(verify.footer(list(r.values())))

# D: the check is local (tracking ref), so an unreachable remote changes nothing; a claimed push that left the ref behind is red
sh("git", "remote", "set-url", "origin", f"{tmp}/nope.git", cwd=repo)
r = verify.check("Pushed to the repo.", [c2, push_call])
assert r[0]["verdict"] == "bad", r

# E: wrote
open(f"{tmp}/out.txt", "w").write("hello")
assert verify.check(f"I wrote `{tmp}/out.txt` for you.", [{"name": "write_file", "args": {"path": f"{tmp}/out.txt"}}])[0]["verdict"] == "ok"
assert verify.check(f"I wrote `{tmp}/gone.txt` for you.", [])[0]["verdict"] == "bad"

# J: review fixes
assert verify.repo_hint([{"args": {"command": "cd sub && git push"}}]) is None                 # relative: no cwd to resolve against
assert verify.repo_hint([{"args": {"command": f"cd /nonexistent && cd {repo} && git push"}}]) == repo   # last cd wins
assert verify.repo_hint([{"args": {"command": f"git -C {repo} push origin main"}}]) == repo
assert verify.probe_pushed(repo, [{"args": {"command": "git push upstream main"}}])[0] == "unknown"    # a different remote was pushed to
assert verify.probe_pushed(repo, [{"args": {"command": "git push origin HEAD:other"}}])[0] == "unknown"
sh("git", "checkout", "-q", "--detach", cwd=repo)
assert verify.probe_pushed(repo)[0] == "unknown"                                                       # detached HEAD
sh("git", "checkout", "-q", "main", cwd=repo)
open(f"{tmp}/old.txt", "w").write("x"); os.utime(f"{tmp}/old.txt", (1_000_000, 1_000_000))
assert verify.probe_wrote(f"{tmp}/old.txt", time.time())[0] == "unknown"                              # existed before this turn
assert verify.probe_wrote("rel/path.py")[0] == "unknown"
os.environ["TERMINAL_ENV"] = "docker"
assert verify.check(f"I wrote `{tmp}/gone.txt` for you.", [])[0]["verdict"] == "unknown"               # host probes would look at the wrong machine
del os.environ["TERMINAL_ENV"]

# H: a chat-only turn started inside the repo: cwd is the fallback, a recent HEAD counts as the commit
sh("git", "remote", "set-url", "origin", remote, cwd=repo)
cwd0 = os.getcwd(); os.chdir(repo)
r = {x["type"]: x for x in verify.check("Committed and pushed to origin main.", [])}
os.chdir(cwd0)
assert r["pushed"]["verdict"] == "bad" and r["committed"]["verdict"] == "unknown", r  # no commit made this turn: not evidence

# I: the reply itself names the repo
r = {x["type"]: x for x in verify.check(f"Committed and pushed note.txt in {repo} to origin main.", [])}
assert r["pushed"]["verdict"] == "bad" and r["committed"]["verdict"] == "unknown", r  # no commit made this turn: not evidence

# L: combined verdicts (assert on check(), not on judge() alone)
old = f"{tmp}/prior.txt"; open(old, "w").write("x"); os.utime(old, (1_000_000, 1_000_000))
w = {"name": "write_file", "args": {"path": old}, "result": "", "exit": 0, "ts": time.time()}
r = verify.check(f"Wrote `{old}`.", [w])
assert r[0]["verdict"] == "unknown", r                      # file predates the turn; a call naming it is not a write
push0 = {"name": "terminal", "args": {"command": "git push"}, "result": "", "exit": 0, "ts": time.time()}
cwd1 = os.getcwd(); os.chdir(tmp)
r = verify.check("Pushed to the repo.", [push0]); os.chdir(cwd1)           # no repo found: a bare exit 0 must not become a green mark
assert r[0]["verdict"] == "unknown" and "no git push ran" not in verify.line(r[0]), r
os.chdir(repo); r = verify.check("Committed the work in the repo.", []); os.chdir(cwd0)
assert r[0]["verdict"] == "unknown", r

# F: a hung probe cannot cost the reply: budget holds
verify.probe_pushed = lambda *a: time.sleep(30)
verify.BUDGET = 1.0
t0 = time.time()
r = verify.check("Pushed to the repo.", [c2, push_call])
assert time.time() - t0 < 2.5 and r[0]["verdict"] in ("unknown", "ok") and (r[0]["why"] == "timeout" or r[0]["verdict"] == "ok"), (r, time.time() - t0)
# G: no claims -> nothing to say
assert verify.check("Here is the plan for tomorrow.", []) == []
# K: a warning line never carries accusation language
for claim_reply, cs in (("Pushed to the repo.", []), ("Pushed to the repo.", [{"name": "terminal", "args": {"command": "ls"}, "result": "", "exit": 0, "ts": time.time()}])):
    for x in verify.check(claim_reply, cs, history=[]):
        if x["verdict"] == "unknown":
            assert "no git push ran" not in verify.line(x), verify.line(x)
print("verify: all checks pass")
