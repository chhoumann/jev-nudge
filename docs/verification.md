# Verification - 2026-09-20

Tested on macOS using Codex CLI 0.155.1, Python 3.14.7, and TypeSafe `jev-1.13.0`. Threshold: 0.5.

## Automated checks

- Ruff lint and formatting checks pass.
- 18 pytest cases pass. Coverage includes threshold boundaries, continuation limits, duplicate events, new user turns, interruption/disable guards, transcript retention and redaction, compaction summaries, installer preservation/idempotency/permissions, malformed probabilities, timeouts, and fail-open CLI behavior.
- CI runs these deterministic checks on Linux. It does not use API credentials.

## Live Jev evaluation

The final prompt passed 16/16 targeted cases and 8/8 fresh holdout cases. Median request latency was 716 ms and 725 ms respectively. These are small, synthetic fixtures labeled during development, not a measured production accuracy rate.

The original prompt falsely nudged a completed plan-only response (0.67). Clarifying requested deliverables corrected that case but made an already-authorized implementation offer fall below threshold. Adding the distinction between genuine permission gates and unnecessary offers passed both. The baseline, intermediate, final, and holdout scores are retained in `evals/`.

The holdout cases cover review-only requests, a Danish stop request, partial implementation, prohibited production restarts, cancellation of an earlier task, an interjected question during work, verified completion, and a missing upload. Some positive cases remain close to 0.5; a passing fixture is not a guarantee of future behavior.

## Native Codex test

`uv run python scripts/smoke.py --config config.local.json` exercises real Codex and live Jev in isolated fixture directories with no persistent global installation.

1. A test-only developer instruction deliberately induces the initial answer: “I will create result.txt and verify its contents next.” This reproduces the premature-stop shape deterministically; it is not a claim that unmodified Astra naturally failed this simple task.
2. The native Stop event invokes the installed executable with the real transcript. Jev returns 0.72, and the hook returns the native continuation decision.
3. Codex resumes, writes the requested file, and checks its exact bytes. The runner independently verifies `b"done\n"`.
4. Jev returns 0.09 for the completed result and lets Codex stop.
5. A separate normal Codex request asks only for a migration plan. Jev returns 0.05 and allows it to stop without a nudge.

The final native checks took 736 ms, 753 ms, and 716 ms for Jev. Sanitized decision evidence is in `evals/native-2026-09-20.json`; raw transcripts remain in ignored `work/` locally.
