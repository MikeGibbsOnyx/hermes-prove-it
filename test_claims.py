"""One runnable check: python3 test_claims.py"""
from claims import extract, judge

T = lambda s: [c["type"] for c in extract(s)]
# claims we must catch
assert T("Done. I pushed the fix to main on the origin remote.") == ["pushed"]
assert set(T("Committed and pushed to the repo.")) == {"pushed", "committed"}
assert T("I wrote `/tmp/out.txt` for you.") == ["wrote"]
assert "tests" in T("All 12 tests pass now.")
# things that must NOT be claims
for s in ["I haven't pushed yet.", "Want me to push it?", "Once you've pushed, tell me.", "Alice pushed the branch.",
          "It should be pushed after review.", "I'll push when CI is green.", "```\ngit push origin main\n```",
          "Pushing is blocked by the hook.", "If tests pass we merge.", "The branch was pushed earlier.",
          "You can ask me to commit the change.", "I've committed to doing this properly.",
          "I pushed you down on the rug.", "Water being pushed aside by the boat, repo untouched.",
          "That pushed it further from the canon branch.", "Committed (1yr) pricing saves 40%, see the repo docs.",
          "It's all green."]:
    assert T(s) == [], (s, T(s))
# judging
push = {"name": "terminal", "args": {"command": "git push origin main"}, "result": "To github", "exit": 0}
bad_push = dict(push, exit=1)
assert judge({"type": "pushed"}, [])[0] == "unknown"           # chat-only turn: cannot say, never accuse
ls = {"name": "terminal", "args": {"command": "ls"}, "result": "", "exit": 0}
assert judge({"type": "pushed"}, [ls])[0] == "unknown"             # fresh session: earlier work may be out of sight
assert judge({"type": "pushed"}, [ls], history=[ls])[0] == "bad"   # acted, never pushed, history exists
assert judge({"type": "committed"}, [{"name": "terminal", "args": {"command": "git status"}, "result": "clean", "exit": 0}])[0] == "unknown"
assert judge({"type": "pushed"}, [{"name": "terminal", "args": {"command": "rsync -a a b"}, "result": "", "exit": 0}])[0] == "skip"
assert judge({"type": "pushed"}, [push])[0] == "ok"
assert judge({"type": "pushed"}, [], history=[push])[0] == "ok"   # recap of an earlier turn is not a lie
assert judge({"type": "pushed"}, [{"name": "terminal", "args": {"command": "gh repo create x --push"}, "result": "", "exit": 0}])[0] == "ok"
assert judge({"type": "committed"}, [{"name": "terminal", "args": {"command": "git commit -m x && false"}, "result": "[main abc1234] x", "exit": 1}])[0] == "ok"
assert extract("pushed, committed, file written, deployed or URL live, tests pass.") == []
assert judge({"type": "pushed"}, [bad_push])[0] == "bad"
assert judge({"type": "pushed"}, [{"name": "delegate_task"}])[0] == "skip"
pt = {"name": "terminal", "args": {"command": "pytest -q"}, "result": "== 12 passed in 0.4s ==", "exit": 0}
assert judge({"type": "tests"}, [pt])[0] == "ok"
assert judge({"type": "tests"}, [dict(pt, result="1 failed, 11 passed", exit=1)])[0] == "bad"
piped = {"name": "terminal", "args": {"command": "pytest | tail -3"}, "result": "done", "exit": 0}
assert judge({"type": "tests"}, [piped])[0] == "unknown"   # never certify from an ambiguous log
assert judge({"type": "wrote", "path": "/definitely/not/here.txt"}, [])[0] == "bad"
# review fixes
assert judge({"type": "pushed"}, [bad_push], history=[push])[0] == "bad"          # this turn's failure beats an earlier success
assert judge({"type": "pushed"}, [dict(push, exit=None, result="")])[0] != "ok"   # unknown exit is not success
assert judge({"type": "pushed"}, [{"name": "read_file", "args": {"path": "README", "note": "git push origin"}, "result": "", "exit": 0}], history=[ls])[0] == "bad"
t_ok = {"name": "terminal", "args": {"command": "cargo test"}, "result": "test result: ok. 10 passed; 0 failed", "exit": 0}
assert judge({"type": "tests"}, [t_ok])[0] == "ok"                                 # "0 failed" is not a failure
assert judge({"type": "tests"}, [dict(t_ok, args={"command": "pytest | tail -3"})])[0] == "unknown"
assert judge({"type": "tests"}, [])[0] == "unknown" and judge({"type": "tests"}, [ls], history=[t_ok])[0] == "unknown"
assert extract("CI says: all tests passed.") == [] and extract("> I ran it. Tests pass.") == []
assert extract("Pushed nothing to origin yet.") == [] and extract("The branch was pushed by CI on origin.") == []
assert extract("Pushed via GitHub API + token to the repo.") == []
print("claims: all checks pass")
