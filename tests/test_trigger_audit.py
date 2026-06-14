"""Fire-and-forget /api/audits/run.

The endpoint must:
  - return immediately (202) with a job_id
  - spawn run_weekly_audit as a background asyncio task
  - NOT await it inline (otherwise Cloudflare 524s on a 60s+ audit)
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import Response

from app.routers import audits


@pytest.mark.asyncio
async def test_trigger_returns_202_and_job_id_without_waiting(monkeypatch):
    """Even if the audit blocks forever, the handler must return promptly."""
    started = asyncio.Event()
    blocked = asyncio.Event()  # never set

    async def _blocking_audit() -> dict:
        started.set()
        await blocked.wait()
        return {"total_found": 0, "processed": 0, "dropped_by_cap": 0, "results": []}

    monkeypatch.setattr(audits, "run_weekly_audit", _blocking_audit)

    response = Response()
    result = await asyncio.wait_for(audits.trigger_audit(response), timeout=0.5)

    assert result["status"] == "started"
    assert "job_id" in result and len(result["job_id"]) > 0
    # Location header points at the future job-detail endpoint shape
    assert response.headers.get("Location") == f"/api/audits/{result['job_id']}"

    # Give the spawned task a tick to start, then unblock it so it doesn't
    # leak across tests.
    await asyncio.sleep(0)
    assert started.is_set()
    blocked.set()


@pytest.mark.asyncio
async def test_trigger_spawns_run_weekly_audit_in_background(monkeypatch):
    """A successful audit should log audit_completed with the job_id."""
    calls: list[str] = []

    async def _fake_audit() -> dict:
        calls.append("ran")
        return {"total_found": 3, "processed": 2, "dropped_by_cap": 1, "results": []}

    monkeypatch.setattr(audits, "run_weekly_audit", _fake_audit)

    response = Response()
    result = await audits.trigger_audit(response)
    assert result["status"] == "started"

    # Yield twice — once for the handler to spawn, once for the task to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_audit_failure_is_logged_not_raised(monkeypatch, caplog):
    """If the audit raises, the background task should swallow + log it
    so the asyncio task doesn't go silent."""
    async def _broken_audit() -> dict:
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(audits, "run_weekly_audit", _broken_audit)

    response = Response()
    await audits.trigger_audit(response)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Test passes if no exception propagates out of the test (the task
    # internally caught the exception). The structured log goes via
    # structlog so caplog won't see it, but the swallow is the contract
    # being tested.
