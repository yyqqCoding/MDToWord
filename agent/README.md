# MD To Word Agent

The Agent is implemented in independently verifiable stages. Stage A provides the domain,
configuration, feedback repository, artifact, fingerprint, version, and database foundations.

## Development

From the repository root, install the Agent package in a dedicated environment and run:

```bash
python -m pytest agent/tests -q
```

The SQL migration is [migrations/001_agent_foundation.sql](migrations/001_agent_foundation.sql).
It is intentionally not applied by tests or application startup. Apply it manually with the
Supabase database owner after reviewing it and take a database backup first.

The Feedback API credential and `SUPABASE_AGENT_KEY` must be different. The latter is only for
the self-hosted Controller and must never be exposed to the browser extension or backend API.
