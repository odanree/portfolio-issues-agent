"""FastAPI entrypoint.

Lifespan:
  1. configure structlog
  2. init Postgres engine + create_all
  3. init Redis (for LangGraph checkpointer)
  4. compile graph with AsyncRedisSaver (MemorySaver fallback for dev)
  5. inject AgentDeps so nodes can reach anthropic/github/slack/db
  6. start APScheduler weekly cron (unless audit_cron_day_of_week is empty)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from anthropic import Anthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agent.graph import init_graph
from app.agent.nodes import AgentDeps, inject_deps
from app.config import get_settings
from app.db.session import init_db
from app.routers import audits as audits_router
from app.routers import health as health_router
from app.routers import slack_actions as slack_actions_router
from app.services.github_client import GitHubClient
from app.services.slack_client import SlackClient

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ]
    )

    await init_db()

    # LangGraph checkpointer
    checkpointer = None
    checkpointer_ctx = None
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        checkpointer_ctx = AsyncRedisSaver.from_conn_string(settings.redis_url)
        checkpointer = await checkpointer_ctx.__aenter__()
        await checkpointer.asetup()
        log.info("checkpointer_initialized", backend="AsyncRedisSaver")
    except Exception as e:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
        log.warning(
            "checkpointer_fallback",
            reason=str(e),
            backend="MemorySaver",
        )
    app.state.checkpointer = checkpointer
    app.state._checkpointer_ctx = checkpointer_ctx

    init_graph(checkpointer)

    # Service clients
    github = GitHubClient(token=settings.github_token)
    slack = SlackClient(bot_token=settings.slack_bot_token)
    anthropic = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else Anthropic()

    from app.db.session import AsyncSessionLocal

    inject_deps(
        AgentDeps(
            anthropic=anthropic,
            model=settings.agent_model,
            github=github,
            slack=slack,
            slack_channel=settings.slack_channel_id,
            db_session_factory=AsyncSessionLocal,
        )
    )
    app.state.github = github
    app.state.slack = slack

    # Scheduler
    scheduler: AsyncIOScheduler | None = None
    if settings.audit_cron_day_of_week:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            audits_router.run_weekly_audit,
            CronTrigger(
                day_of_week=settings.audit_cron_day_of_week,
                hour=settings.audit_cron_hour,
            ),
            id="weekly_audit",
            name="weekly portfolio drift audit",
        )
        scheduler.start()
        log.info(
            "scheduler_started",
            day_of_week=settings.audit_cron_day_of_week,
            hour=settings.audit_cron_hour,
        )
    app.state.scheduler = scheduler

    log.info("startup_complete", env=settings.app_env)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await github.close()
        await slack.close()
        if checkpointer_ctx is not None:
            try:
                await checkpointer_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        log.info("shutdown_complete")


app = FastAPI(
    title="portfolio-issues-agent",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", path=str(request.url.path), error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health_router.router)
app.include_router(audits_router.router)
app.include_router(slack_actions_router.router)
