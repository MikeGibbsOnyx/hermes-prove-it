"""Claim extraction and static judging. Pure functions, no model, no network.
Precision over recall: a claim we are unsure about is skipped, never guessed."""
from __future__ import annotations

import json
import os
import re

_FENCE = re.compile(r"```.*?```", re.S)
_BOUNDARY = re.compile(r"\.(?=\s|$)|[;:!?\n|\u2014]|\s-\s")
# words before the verb that make it not-a-completion-claim (negation, future, condition, past-elsewhere)
_HEDGE = re.compile(
    r"\b(?:not|never|no|nor|without|yet|before|until|once|after|if|whenever|when|unless|whether|"
    r"to|will|would|should|shall|can|cannot|could|may|might|must|need|needs|needed|ready|about|plan|"
    r"planning|going|gonna|want|wants|please|already|earlier|previously|yesterday|then|was|were|"
    r"be|had|haven|hasn|didn|don|doesn|won|nothing|none)\b|n't|let's", re.I)
_OTHER_SUBJ = re.compile(
    r"\b(?:he|she|they|you|it|ci|actions|github|gh|someone|nobody|user|we're|"
    r"[A-Z][a-z]+\s+(?:and|&)\s+[A-Z][a-z]+)\s+(?:\w+\s+){0,2}$", re.I)
# a claim sentence is short: only these words may precede the verb ("Build clean, lint clean, pushed.")
_FILLER = {"i", "ive", "we", "weve", "all", "and", "now", "just", "also", "build", "lint", "clean", "types",
           "typecheck", "tests", "test", "passes", "passing", "verified", "fixed", "done", "commit", "commits",
           "changes", "everything", "successfully", "finally", "really", "the", "my", "our", "files", "work",
           "docs", "fix", "code", "site", "branch", "repo", "created", "merged", "committed", "pushed", "force", "is", "are", "been", "has", "have", "it", "ok", "yes", "yeah"}
_SPEECH = re.compile(r"\b(?:says?|said|reports?|reported|shows?|showed|output|prints?|printed|claims?|claimed|states?|stated|reads?|example|e\.g\.|like)\W*$", re.I)
_QUOTES = "\"\u201c\u201d'\u2018`"
_PATH = r"`?((?:~|\.{0,2})/[\w.~/@+-]+|[\w@+-][\w.@+-]*\.[A-Za-z0-9]{1,6})`?"
_GIT_CTX = re.compile(r"\b(?:git|github|gitlab|repo|repository|branch|origin|upstream|remote|commits?|pull request|PR)\b|\b[0-9a-f]{7,40}\b", re.I)
_NOT_GIT_OBJ = r"(?!\s+(?:you|him|her|them|it|me|us|nothing|anything|by|away|aside|back|further|through|forward|harder|hard|right|so|too|all|down|up|out|in|off|past|against|into)\b)"
_PATTERNS = {
    "pushed": re.compile(r"(?<![\w-])(?:force-)?pushed\b" + _NOT_GIT_OBJ + r"|\bpush\s+(?:succeeded|went through|landed)\b", re.I),
    "committed": re.compile(r"\bcommitted\b(?!\s+(?:to|use|\()\b)(?!\s*\()", re.I),
    "wrote": re.compile(r"\b(?:wrote|created|saved|written|generated)\s+(?:the\s+|a\s+|new\s+)?(?:file\s+)?`([^`\n]*(?:/|\.[A-Za-z0-9]{1,6})[^`\n]*)`", re.I),
    "tests": re.compile(
        r"\b(?:all\s+)?(?:\d+\s+)?tests\s+(?:are\s+|now\s+)?(?:pass(?:ed|ing)?|green)\b|"
        r"\bsuite\s+(?:is\s+)?(?:green|passing)\b", re.I),
}
_TEST_CMD = re.compile(r"\b(?:pytest|py\.test|unittest|npm\s+(?:run\s+)?test|yarn\s+test|cargo\s+test|go\s+test|jest|vitest|make\s+test|tox)\b")
_PIPED = re.compile(r"\b(?:pytest|py\.test|unittest|npm\s+(?:run\s+)?test|yarn\s+test|cargo\s+test|go\s+test|jest|vitest|make\s+test|tox)\b[^\n;&]*\|")
_PUSH_CMD = re.compile(r"\bgit\b[^\n|;&]*\bpush\b")
_COMMIT_CMD = re.compile(r"\bgit\b[^\n|;&]*\bcommit\b")
_POSITIVE_TEST = re.compile(r"\b\d+\s+passed\b|\bOK\b|\bAll tests passed\b|\btests? passed\b", re.I)
_NEGATIVE_TEST = re.compile(r"\b[1-9]\d*\s+(?:failed|errors?)\b|(?-i:\bFAILED\b|\bFAIL\b)|Traceback", re.I)


_KINDS = [re.compile(p, re.I) for p in (r"\bpush(?:ed)?\b", r"\bcommit(?:ted)?\b", r"\bdeploy(?:ed)?\b",
          r"\b(?:url|site)\s+(?:is\s+)?live\b", r"\btests?\s+pass", r"\bmessage\s+(?:sent|was delivered)\b",
          r"\bfile\s+written\b")]
_TRANSFER = re.compile(r"\b(?:rsync|scp|sftp|rclone|gsutil|hf\s+upload|huggingface-cli\s+upload|aws\s+s3|docker|podman|skopeo|crane|npm|pnpm|twine|vercel|flyctl|kubectl|helm|cargo\s+publish)\b")
_PUSH_OK = re.compile(r"->|Everything up-to-date|\bnew branch\b")
_COMMIT_OK = re.compile(r"\[[\w./-]+(?:\s+\(root-commit\))?\s+[0-9a-f]{7,}\]|\d+ files? changed")


def extract(reply: str, git_hint: bool = False) -> list[dict]:
    """Completion claims in a final reply: [{type, sentence, path?}]."""
    text = re.sub(r"(?m)^\s*>.*$", " ", _FENCE.sub(" ", reply or ""))  # fences and blockquotes are not asserted
    out = []
    git_ctx = git_hint or bool(_GIT_CTX.search(text))
    for typ, pat in _PATTERNS.items():
        if typ in ("pushed", "committed") and not git_ctx:
            continue
        for m in pat.finditer(text):
            starts = [b.end() for b in _BOUNDARY.finditer(text, 0, m.start())]
            start = starts[-1] if starts else 0
            end = _BOUNDARY.search(text, m.end())
            sent = text[start:end.start() if end else len(text)].strip()
            window = text[start:m.start()]
            if _SPEECH.search(text[max(0, start - 40):start]):
                continue  # 'CI says: all tests passed' is someone else's statement
            if _HEDGE.search(window) or _OTHER_SUBJ.search(window):
                continue
            if window.count("(") > window.count(")"):
                continue  # inside a parenthetical or definition
            before = text[m.start() - 1] if m.start() else " "
            after = text[m.end()] if m.end() < len(text) else " "
            if before in _QUOTES or (typ != "wrote" and after in _QUOTES) or "=" in window or "+ " in window[-2:]:
                continue  # quoted or defined, not asserted
            words = [w for w in re.findall(r"[a-z']+", window.lower())]
            if any(w.replace("'", "") not in _FILLER for w in words):
                continue
            if sum(1 for k in _KINDS if k.search(sent)) >= 2 and typ in ("pushed", "committed", "tests"):
                if sum(1 for k in _KINDS[2:] if k.search(sent)) >= 1 or sent.count(",") >= 3:
                    continue  # a list of claim types, not an assertion
            if typ == "pushed" and re.match(r"\s+[A-Z]", text[m.end():m.end() + 3]):
                continue  # "pushed Alice": an object, not a git push
            if typ == "pushed" and re.search(r"\b(?:via|through|using|with)\s+(?:the\s+)?(?:github\s+|gitlab\s+)?(?:api|curl|http|rest|web|ui|console)\b", sent, re.I):
                continue  # pushed by some other mechanism than `git push`
            if end and text[end.start()] == "?":
                continue
            if sent.startswith((">", "- If", "* If")):
                continue
            claim = {"type": typ, "sentence": sent[:200]}
            if typ == "wrote":
                if any(ch in m.group(1) for ch in "{*$"):
                    continue
                claim["path"] = m.group(1)
            out.append(claim)
    return out


def _cmd(call: dict) -> str:
    a = call.get("args") or {}
    return a.get("command", "") if isinstance(a, dict) and call.get("name") in ("terminal", "process_manage", "process") else ""


def _blob(call: dict) -> str:
    return json.dumps(call.get("args"), default=str)


def judge(claim: dict, calls: list[dict], exists=os.path.exists, history: list[dict] = ()) -> tuple[str, str]:
    """Static verdict from the turn's tool ledger. ('ok'|'bad'|'unknown'|'skip', reason).
    calls: [{name, args, result, exit}]. Probes (live git/stat) refine this later."""
    if any(c.get("name") in ("delegate_task", "mixture_of_agents") for c in calls):
        return "skip", "turn delegated work; child ledger is separate"
    typ = claim["type"]
    if typ in ("pushed", "committed"):
        pat = _PUSH_CMD if typ == "pushed" else _COMMIT_CMD
        seen = _PUSH_OK if typ == "pushed" else _COMMIT_OK
        def good(c):  # a positive observation, or a recorded exit code of 0; an unknown exit is not success
            return c.get("exit") == 0 or bool(seen.search(str(c.get("result", ""))))
        def find(cs):
            return [c for c in cs if pat.search(_cmd(c)) or (typ == "pushed" and re.search(r"\bgh\s+repo\s+create\b.*--push", _cmd(c)))]
        now = find(calls)
        if any(good(c) for c in now):
            return "ok", f"git {typ} seen this turn"
        if now:  # this turn's own attempt: an earlier success must not paper over a recorded failure
            if any(c.get("exit") is None for c in now):
                return "unknown", f"git {typ} ran; its exit status was not recorded"
            return "bad", f"said {typ}; every matching git call failed"
        if any(good(c) for c in find(history)):
            return "ok", f"git {typ} seen earlier this session"
        if typ == "pushed" and any(_TRANSFER.search(_cmd(c)) for c in list(calls) + list(history)):
            return "skip", "no git push, but a file transfer ran; 'pushed' may not mean git"
        if any(re.search(r"\bgit\b[^\n|;&]*\b(?:log|status|show|ls-remote|diff)\b", _cmd(c)) for c in calls):
            return "unknown", f"said {typ} after only inspecting git; likely a state report on earlier work"
        if not calls:  # a chat-only turn: the claim may describe earlier work this ledger never saw
            return "unknown", f"said {typ}; nothing in this session's record, and no action this turn to check"
        if not history:  # fresh or compacted session: earlier work may be out of sight
            return "unknown", f"said {typ}; no earlier record in this session to check against"
        return "bad", f"said {typ}; the turn acted but no git {'push' if typ == 'pushed' else 'commit'} ran, here or earlier"
    if typ == "wrote":
        path = os.path.expanduser(claim.get("path", ""))
        base = os.path.basename(path)
        if (path.startswith("/") or path.startswith("~")) and not exists(path):  # relative: no cwd to resolve against
            return "bad", f"said wrote {claim['path']}; no such file"
        mention = any(base and base in _blob(c) for c in calls)
        if mention:
            return "ok", "a tool call this turn names the file"
        return "unknown", "file named but no tool call this turn mentions it"
    if typ == "tests":
        runs = [c for c in calls if _TEST_CMD.search(_cmd(c))]
        if not runs:
            if not calls or any(_TEST_CMD.search(_cmd(c)) for c in history):
                return "unknown", "said tests pass; no test run this turn (earlier or chat-only)"
            return "bad", "said tests pass; no test command ran this turn"
        if any(_PIPED.search(_cmd(c)) and "pipefail" not in _cmd(c) for c in runs):
            return "unknown", "test run was piped; the exit code is the pipe's, not the tests'"
        for c in runs:
            out = str(c.get("result", ""))
            if _NEGATIVE_TEST.search(out) or c.get("exit") not in (0, None):
                return "bad", "a test run this turn shows failures or a nonzero exit"
        if any(_POSITIVE_TEST.search(str(c.get("result", ""))) for c in runs):
            return "ok", "test output shows passes, no failures"
        return "unknown", "test ran but output has no pass count"
    return "skip", "unknown claim type"
