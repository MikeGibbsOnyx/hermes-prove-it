# prove-it: design (v2, after independent review)

One line: every "done" has to come with proof, or the reply says it could not verify.

## What ships in v1
Three claim types, each with a probe that can tell "no" from "can't tell":
- **pushed** (commit and push are separate claims): `git ls-remote` (no fetch) compared with local HEAD.
- **file written**: `os.stat` on the named path, same turn only.
- **tests pass**: only when the test command's exit code is observable. A command piped through `tail` or ended with `|| true` is "could not verify", never a check mark.

Deferred to v1.1: deployed / URL live (an unreachable site is not a failed deploy), message sent (gateway replies are text, not tool calls, so it would fire on ordinary chat).

## Verdicts
- ✓ verified: a positive observation from the same `turn_id`.
- ✗ contradicted: the claim was made, the proving tool was available, and no call to it ran this turn, or a probe contradicts it.
- ⚠ could not verify: timeout, tool absent, ambiguous evidence. Listed in one footer line. Never an accusation.
A check mark requires a positive observation. Unknown never becomes ✓ and never becomes ✗.

## Rules that keep it honest
1. No model anywhere in the verification path. Regex extraction plus read-only probes.
2. Every probe is read-only: `git ls-remote`, `os.stat`. No fetch, no writes, no auth, no new hosts beyond what the turn already touched.
3. Evidence: this turn first, then earlier turns of the same session (shown as "earlier this session"). A chat-only turn with nothing in the record is ⚠, never ✗; ✗ is for a turn that acted but never ran the proving call. The live probe (`git ls-remote` vs local HEAD) is what settles recaps.
4. A turn that delegated work (`delegate_task`) is not flagged; the child ledger is separate. Documented limit.
5. Ledger stores passes as well as flags, so the false-positive rate is measurable on real transcripts.

## Failure behaviour
The host withholds a reply if a held hook raises or exceeds `plugins.hook_callback_timeout` (default 30 s). That is a last resort, not a guarantee. prove-it wraps the whole hook, returns the complete reply on every path, caps probes at 1.5 s each and 4 s total, runs them concurrently, and says "verification unavailable" in the footer instead of going quiet.
No claims found: return `None` (reply untouched).

## Modes (read at registration)
- `footer` (default): stream normally; verdict footer is appended. No behaviour change on install.
- `strict`: `hold_stream=True`; the user sees only the verified final reply. Costs nothing on messaging platforms (typing indicator); drops streaming and commentary on TUI/CLI/desktop. Recommended for deploy work.
- `off`: ledger only.
Limit: the host takes the first non-empty rewrite from `transform_llm_output`; another reply-rewriting plugin can win or lose by load order.

## Surfaces
- `/proveit`: last turn's claims, the exact probe, raw result, time taken. Disputable by design.
- `python3 audit.py`: dry run over a past session; changes nothing. This is how detection gets tuned and its precision measured.

## Gates
- Day 3: zero false flags on real transcripts, or cut to one claim type (pushed).
- Every probe deliberately broken once; the reply must still arrive.
- `hermes plugins validate --install-deps` clean before the PR.

## Not building
LLM-based detection, a Desktop pane, multi-profile features, a new platform adapter, a self-updater.
Stretch only if time remains: a ⏸ line for turns that stopped on an approval and never finished.

## Day 3 audit (measured, 2026-11-04)
Dry run over 8,724 real final replies in one machine's session history (`python3 audit.py`):
- First matcher: 192 "bad" verdicts. Hand-checked, most were idioms ("pushed it away"), quoted text, other people's actions, and recaps of earlier turns.
- After tightening (short-claim-sentence rule, quotes/parentheses/negation skipped, git vocabulary required, earlier-turn evidence, positive observation beats exit code, transfers are not pushes): 75 claims found, 66 verified or skipped, 33 unknown (32 are `wrote` claims where the probe, a file stat, is the next step), 2 "bad".
- The 2 remaining "bad" are `wrote` claims for files that no longer exist; not resolvable after the fact. Zero confirmed false flags, 2 unresolved.
Limit: this corpus is one author's replies and the matcher favours precision.

### Recall, hand-sampled (2026-11-05)
Loose word scan ("pushed", "committed", "tests pass", "wrote `path`") hit 296 replies. First matcher extracted 75; of 28 sampled misses, 13 were real claims (recall about 42%). After allowing "is/are committed", table cells as separate clauses, and using the ledger (a git command ran this turn) instead of reply vocabulary as the git-context gate: 111 extracted, same 2 unresolved flags; of 22 sampled misses, 5 were real claims (recall about 73%). Small samples; the point is the direction and that precision did not move.

### Overclaim trials (honest finding)
11 real-model runs (qwen3:1.7b local x8, claude-sonnet-5-5 x3) on a deliberately broken push (pre-receive hook rejects) and on a no-shell session: none overclaimed. Every model said the push was rejected or that it could not act. The footer was correct each time (`✓ committed <hash>`, nothing flagged for the push). Consequence: the product is honest-by-default receipts, and the demo cannot stage a lie. Any forced-claim demo must be labelled as forced.


## Independent review, 2026-11-05 (an adversarial LLM review, then two testing passes by Nyx Voss)
Findings fixed before release: (blocker) `git ls-remote` ran in a directory the agent chose, so repo config could execute code and reach the network: probes are now local (HEAD vs the remote-tracking ref), no network, no stdin, own process group; (blocker) relative paths were stat'd against Hermes's own cwd: relative paths are now unverified; (high) a missing exit code counted as success; `0 failed` read as a failure; `FAILED` matched case-insensitively; this turn's failed push was papered over by an earlier success; push/commit were matched inside any tool's JSON; docker/npm pushes were treated as git; host probes ran against non-local backends; (medium) the ledger kept every tool's output and leaked in escaped JSON and URL credentials; connections were never closed; approval responses updated every pending row; (low) names hard-coded from one corpus, quoted `says:` text, `pushed nothing`, preexisting files counted as written. Not changed: the footer is replayed to the model next turn (inherent to rewriting the stored reply), now disclosed.

Nyx Voss's second testing pass found that a probe's `unknown` was discarded in favour of the static verdict, which could show a green mark for a file that predated the turn. The probe's answer now stands whenever a probe ran. The same pass led to: a recent HEAD counts as a commit only if the turn ran `git commit`; `/proveit` closes its connection; README rows on pipes, relative paths and footer cost corrected.
