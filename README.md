# Jev Nudge

A native Codex Stop hook that asks Jev whether unfinished, already-authorized work would benefit from a gentle nudge. When `P(yes) >= 0.5`, Codex receives a continuation prompt and carries on.

Inspired by [Will Brown's post](https://x.com/willcb/status/2101178888441516117), his [classifier prompt](https://x.com/willcb/status/2101195579204448750), and [threshold](https://x.com/willcb/status/2101228014923346226). This is an independent implementation. The post did not include the actual nudge wording.

## Setup

Requires macOS or Linux, Python 3.11+, uv, a TypeSafe API key, and Codex with native Stop hooks. Tested with Codex CLI 0.155.1 and `jev-1.13.0`.

```sh
uv sync --locked
cp .env.example .env
chmod 600 .env
# Set TYPESAFE_API_KEY in .env, or provide it in the environment.
cp config.example.json config.local.json
uv run jev-nudge doctor --config config.local.json
```

No runtime dependencies. The HTTP integration follows the [TypeSafe API contract](https://docs.typesafe.ai/api); the question uses a [Noul probability](https://docs.typesafe.ai/primitives/noul).

## Try it

```sh
uv run pytest
uv run ruff check .
uv run jev-nudge eval --config config.local.json evals/cases.json
uv run jev-nudge eval --config config.local.json evals/holdout.json
uv run python scripts/smoke.py --config config.local.json
```

The eval and smoke commands make paid API calls. The smoke test uses your Codex login, creates disposable fixtures under `work/`, deliberately induces a premature answer, and verifies that the native Stop hook gets Codex to create and check the requested file. A second run verifies that a completed plan-only request stops.

The smoke runner vets and enables only its own inline hook with Codex's documented one-invocation trust override. It does not install a global hook or weaken the agent's workspace sandbox. It retains raw evidence under the ignored `work/` directory.

## Enable in Codex

**Enabling the hook sends bounded text excerpts from conversations in that scope to TypeSafe whenever Codex stops.** Choose the desired scope explicitly.

For one project:

```sh
uv run jev-nudge install --config config.local.json --project /absolute/path/to/project
```

For all local Codex projects:

```sh
uv run jev-nudge install --config config.local.json --user
```

Then open `/hooks` in Codex, review the `Jev Nudge: checking unfinished work` entry, and trust it. Project hooks also require a trusted project. Codex may need a new session to load changes. See [Codex hooks](https://learn.chatgpt.com/docs/hooks).

Installation adds one Stop handler, preserves other hooks, and makes a timestamped backup before editing an existing hooks file. Reinstalling is idempotent. The command uses absolute paths to this checkout's virtual environment and config, so keep the checkout in place.

Cloning or syncing this repository does not install global hooks. Persistent activation is a separate step and sends conversation excerpts to TypeSafe within the selected scope.

## Behavior

- Keep earlier user requests, compaction summaries, recent messages and tool evidence, plus the final response.
- Ignore internal system/developer context and reasoning. Only text is supported.
- Use the post's classifier with explicit distinctions for plan-only work and unnecessary permission offers.
- Send a soft continuation only for useful work within existing authorization.
- Limit continuation to three nudges per user turn; deduplicate repeated stop events and serialize checks per session.
- Let Codex stop on API errors, missing credentials, malformed responses, unsupported transcripts, or the continuation limit.
- Respect the native Interrupt lifecycle: this hook runs only on Stop.

The default API timeout is eight seconds, with a 20-second native hook timeout and no retries. The model is pinned so evaluation results do not silently drift with an alias.

`config.local.json` controls `model`, `threshold`, `max_nudges` (1-10), `timeout_seconds` (up to 15), `enabled`, `env_file`, and `state_dir`. Relative paths resolve against the config file, not the agent's current directory.

## Inspect or disable

Check a specific Codex JSONL transcript without continuing the agent:

```sh
uv run jev-nudge check --config config.local.json /path/to/transcript.jsonl \
  --last-message "I will implement it next."
```

Decision metadata is appended to `.state/decisions.jsonl`: probabilities, decisions, model, token usage, latency, session/turn IDs, and truncation flags. Logs exclude prompts, transcript excerpts, and API keys.

Set `"enabled": false` in `config.local.json` to disable immediately, or launch Codex with `JEV_NUDGE_DISABLED=1`. Remove only this handler with:

```sh
uv run jev-nudge uninstall --config config.local.json --user
# Or use --project /absolute/path/to/project for a project installation.
```

## Limits

This is a fallible classifier, not proof of completion or an authorization mechanism. The nudge reiterates existing boundaries. It cannot make a blocked task possible or guarantee the agent obeys.

Transcript excerpts are capped at 24 KB. The first request and recent requests are retained separately; long messages and old context can be omitted. Transcripts above 32 MiB are skipped. This implementation reads Codex's legacy JSONL `response_item` format; transcript-v2 support is not claimed. Images and audio are not evaluated.

Known credential strings and common credential assignments are redacted on a best-effort basis. This is not comprehensive secret detection: conversation text and code may still be sensitive. Credentials stay in the ignored `.env`; local state and raw smoke evidence are also ignored by Git.

See [verification results](docs/verification.md) for measured behavior and the small synthetic evaluation's limits.
