# portfolio-issues-agent

LangGraph + Slack HITL agent that turns [portfolio-drift-agent](https://github.com/odanree/portfolio-drift-agent) findings into GitHub issues on the source repos — after a human clicks **Approve** in Slack.

## Why it exists

The procedural drift agent already writes corrections back to Beacon (your profile/resume system). That keeps the profile in sync but never touches the underlying repos. This sibling agent closes the loop in the other direction: same drift findings, but instead of patching Beacon, propose an issue on the offending repo so the README/code state can catch up.

Issues are public and noisy (watchers get pinged), so every proposal goes through human review via Slack Block Kit buttons before any `POST /repos/{owner}/{repo}/issues` call.

## Flow

```
APScheduler (weekly cron)
  -> /api/audits/run
     for each Beacon project with drift:
       start_workflow(report)
         classify -> draft -> notify_slack -> [INTERRUPT_BEFORE create_issue]
         (state checkpointed to Redis, thread_id = run_id)
         posts Block Kit approval message to Slack

POST /api/slack/actions (Slack webhook)
  verify HMAC signature
  resume_workflow(run_id, approved, reviewer)
    -> create_issue (only if approved) -> post_result -> END
```

## Tooling reused

- [`portfolio-drift-agent`](https://github.com/odanree/portfolio-drift-agent) — pulled as a git dependency; this agent does not reimplement the analyzer, GitHub fetch, or Beacon adapter.
- LangGraph `interrupt_before` + `aupdate_state` + `ainvoke(None)` resume pattern (same shape as [shopify-inventory-discrepancy-agent](https://github.com/odanree/shopify-inventory-discrepancy-agent)).

## Local development

```bash
cp .env.example .env   # fill in BEACON_JWT, ANTHROPIC_API_KEY, GITHUB_TOKEN, SLACK_*
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```

`pytest` runs hermetic (Redis via fakeredis, Postgres via SQLite, all HTTP via respx).

To kick a manual audit:

```bash
curl -X POST http://localhost:8000/api/audits/run
```

## Env vars

- `DATABASE_URL` — `postgresql+asyncpg://...` (or `sqlite+aiosqlite:///./issues.db` for local)
- `REDIS_URL` — `redis://host:port/db` (db 3 on portfolio-infra)
- `ANTHROPIC_API_KEY` — Claude key for drift analysis + issue drafting
- `BEACON_API_URL`, `BEACON_JWT` — Beacon API access (read-only)
- `GITHUB_TOKEN` — PAT with `repo` scope (or `public_repo` if your audited repos are all public)
- `SLACK_BOT_TOKEN` — `xoxb-…`, used for `chat.postMessage`
- `SLACK_SIGNING_SECRET` — for verifying the webhook
- `SLACK_CHANNEL_ID` — channel to post proposals (e.g. `C0123ABCD`)

## Deployment

Lives in [portfolio-infra](https://github.com/odanree/portfolio-infra)'s `docker-compose.yml` as `portfolio-issues-agent`. Caddy fronts the Slack webhook on `issues.danhle.net`. The container runs `uvicorn app.main:app`, which boots an APScheduler weekly job alongside the FastAPI server. Tables are created on startup via SQLAlchemy `create_all` — no Alembic for v1.

## License

MIT
