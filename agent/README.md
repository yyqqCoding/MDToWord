# MD To Word Agent

The Agent is implemented in independently verifiable stages. Stage A provides the persistence
foundation. Stage B1 adds the strict Feedback Gate and local policy. Stage B2 adds a Gate-only
LangGraph, resumable checkpoints, a concurrency-one scheduler, and a fake-provider dry-run CLI.
Stage B3 adds an OpenAI-compatible Provider and fail-open Langfuse Cloud telemetry.

## Development

From the repository root, create the dedicated environment and run:

```bash
uv sync --extra dev
.venv/bin/python -m pytest agent/tests -q
```

The SQL migrations are [001_agent_foundation.sql](migrations/001_agent_foundation.sql) and
[002_gate_runtime.sql](migrations/002_gate_runtime.sql). They are intentionally not applied by
tests or application startup. Apply them manually with the Supabase database owner after review
and backup. Then initialize the third-party checkpoint tables explicitly:

`AGENT_DATABASE_URL` should be a Supabase Direct Connection or Session Pooler DSN. Keep it only
on the Agent Controller; do not expose it to the extension or conversion backend.

```bash
.venv/bin/python -m agent.cli checkpoint setup
```

Stage B2 can run one claimed feedback with the Fake Provider. The safe default is
`needs_human`; other routes are explicit test scenarios. Use a disposable test feedback because
an accepted Fake route intentionally leaves the feedback at `reproducing` for the later stages:

```bash
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --fake-route accepted_backend_bug
```

Stage B3 configuration is listed in the repository root `.env.example`. Copy only the missing
names into the ignored local `.env`; never commit or paste model/Langfuse keys into logs or chat.
`MODEL_BASE_URL` is the API root ending at `/v1`, not the full `/chat/completions` endpoint. If
the compatible provider does not return `usage.cost`, configure the selected model's USD price
per million tokens so `agent_runs.estimated_cost` is meaningful.

After loading `.env`, run a real Gate only against a disposable pending feedback:

```bash
set -a
source .env
set +a
.venv/bin/python -m agent.cli run --feedback-id <uuid> --dry-run \
  --provider configured
```

The real Provider still receives no tools. It uses strict JSON Schema and performs at most one
format-only retry. Provider usage is written to `agent_runs`; Langfuse receives hashes and
structured summaries rather than full Markdown, contact details, prompts, or secrets. A
Langfuse export failure does not change the Gate route. An exhausted model/API failure marks the
run and feedback `failed`, preventing the scheduler from retrying the same run forever.

The Feedback API credential and `SUPABASE_AGENT_KEY` must be different. The latter is only for
the self-hosted Controller and must never be exposed to the browser extension or backend API.
