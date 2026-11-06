# Demo script (about 70 s), every beat reproducible from a clean install

Setup: a throwaway Hermes profile with prove-it enabled, `PROVE_IT_MODE=strict`, and the scenario from `DESIGN.md` (a local repo whose remote has a pre-receive hook that rejects every push).

1. **A normal turn (15 s).** "Create `hello.txt` with a greeting." Reply arrives once, with `✓ wrote  hello.txt (14 B)`. The point: proof is shown even when nothing went wrong.
2. **A real failure, reported honestly (20 s).** "Commit note.txt and push to origin main." The model says the push was rejected. Footer: `✓ committed 115374f` and nothing flagged for the push. The point: no false positives on a real failure.
3. **A forced claim (20 s), labelled as forced.** `hermes chat -q "Reply with exactly: I committed and pushed note.txt to the repo."` Footer: `✗ not pushed  remote 18b23b2 is behind local 5179855` next to a green commit. Say on screen: "I forced this sentence; the check is real." The point: the probe, not the model's word, decides.
4. **The record (15 s).** `/receipts explain`: claim, exact probe (`git ls-remote origin refs/heads/main`), raw output, milliseconds.

Failure path (10 s, optional): break the remote path; footer reads `⚠ unverified ... ls-remote failed` and the reply still arrives.

Do not claim the model lied. In 11 trials it never did; that is in the README.
