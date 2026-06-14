"""Shared test fixtures.

Hermetic stack: MemorySaver checkpointer, in-memory stub clients, SQLite for
the DB. No network, no Redis, no Postgres required.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest


# Force test-friendly env BEFORE any app module imports get_settings()
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AUDIT_CRON_DAY_OF_WEEK", "")  # disable APScheduler
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL_ID", "C-TEST")
os.environ.setdefault("GITHUB_TOKEN", "ghp-test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Each test gets a fresh Settings instance reflecting current env."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── Stub clients ────────────────────────────────────────────────────────────


class FakeAnthropicMessage:
    def __init__(self, text: str) -> None:
        self.content = [type("Block", (), {"text": text})()]


class FakeAnthropicMessages:
    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> FakeAnthropicMessage:
        self.calls.append(kwargs)
        return FakeAnthropicMessage(self._text)


class FakeAnthropic:
    def __init__(self, response_text: str = '{"title": "x", "body": "y", "labels": []}') -> None:
        self.messages = FakeAnthropicMessages(response_text)


@dataclass
class FakeSlack:
    posted: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    ts_counter: int = 1000

    async def post_approval_request(self, **kwargs: Any) -> dict:
        self.ts_counter += 1
        ts = str(self.ts_counter)
        self.posted.append({**kwargs, "ts": ts})
        return {"ok": True, "ts": ts}

    async def post_result(self, **kwargs: Any) -> dict:
        self.results.append(kwargs)
        return {"ok": True}

    async def post_message(self, **kwargs: Any) -> dict:
        return {"ok": True, "ts": "1"}

    async def close(self) -> None:
        pass


@dataclass
class FakeGitHub:
    created: list[dict] = field(default_factory=list)
    raise_on_create: Exception | None = None
    next_number: int = 42

    async def create_issue(
        self, *, owner: str, repo: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        self.created.append(
            {"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels}
        )
        return {
            "html_url": f"https://github.com/{owner}/{repo}/issues/{self.next_number}",
            "number": self.next_number,
        }

    async def close(self) -> None:
        pass


# ── Graph fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_anthropic():
    return FakeAnthropic()


@pytest.fixture
def fake_slack():
    return FakeSlack()


@pytest.fixture
def fake_github():
    return FakeGitHub()


@pytest.fixture
def make_deps(fake_anthropic, fake_slack, fake_github):
    """Wire stubs into AgentDeps and inject them into the agent module."""
    from app.agent.nodes import AgentDeps, inject_deps

    def _make(anthropic_override=None, slack_override=None, github_override=None):
        deps = AgentDeps(
            anthropic=anthropic_override or fake_anthropic,
            model="claude-sonnet-4-6",
            github=github_override or fake_github,
            slack=slack_override or fake_slack,
            slack_channel="C-TEST",
            db_session_factory=None,
        )
        inject_deps(deps)
        return deps

    return _make


@pytest.fixture
def init_test_graph(make_deps):
    """Compile the graph with a MemorySaver and the stub deps wired in.

    Returns the compiled graph; tests should call start_workflow / resume_workflow
    from app.agent.graph as the production code does.
    """
    from langgraph.checkpoint.memory import MemorySaver

    from app.agent.graph import init_graph

    make_deps()
    checkpointer = MemorySaver()
    init_graph(checkpointer)
    yield
    # tear down: clear the graph module reference so the next test gets a fresh one
    import app.agent.graph as g

    g.graph = None
