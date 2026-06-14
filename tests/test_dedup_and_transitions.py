"""Dedup pre-flight check and DB state transitions through the resume path.

Uses an in-memory SQLite session factory wired into app.db.session so the
real repo + audits + resume callbacks all hit the same DB.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import session as db_session
from app.db.base import Base
from app.db.models import IssueProposal  # noqa: F401  (registers metadata)
from app.db.repository import IssueProposalRepository


@pytest.fixture
async def sqlite_db():
    """Spin up an in-memory SQLite, install it as the app's session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    saved = db_session.AsyncSessionLocal
    db_session.AsyncSessionLocal = factory
    try:
        yield factory
    finally:
        db_session.AsyncSessionLocal = saved
        await engine.dispose()


def _payload(project_id: str = "p1") -> dict:
    return {
        "project_id": project_id,
        "project_name": "Test Project",
        "repo_owner": "odanree",
        "repo_name": "test-repo",
        "report": {
            "project_id": project_id,
            "project_name": "Test Project",
            "is_stale": False,
            "has_changes": True,
            "description_suggestion": "add CLIP",
            "outcome_suggestion": None,
            "tech_stack_additions": [],
            "tech_stack_removals": [],
            "canonical_url": None,
            "notes": "",
        },
        "proposed_labels": [],
        "messages": [],
    }


# ── pre-flight dedup ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_skips_before_slack_post_when_pending_exists(
    sqlite_db, init_test_graph, fake_slack, fake_github
):
    """If a pending row already exists for the project, the audit must short-
    circuit BEFORE start_workflow runs — so no Slack message is posted."""
    from app.routers.audits import _process_one

    # Seed a pending row
    async with sqlite_db() as session:
        repo = IssueProposalRepository(session)
        await repo.create_pending(
            run_id="prior-run",
            project_id="p1",
            project_name="Test Project",
            repo_owner="odanree",
            repo_name="test-repo",
            proposed_title="Earlier proposal",
            proposed_body="…",
            proposed_labels=[],
        )

    result = await _process_one(_payload())
    assert result["status"] == "skipped_existing"
    assert "prior-run" in result["open_run_ids"]
    # Most importantly: the graph never ran, so Slack saw nothing.
    assert fake_slack.posted == []
    assert fake_github.created == []


@pytest.mark.asyncio
async def test_dedup_lets_new_project_through(
    sqlite_db, init_test_graph, fake_slack, fake_github
):
    """Different project_id → no dedup hit → graph runs → row persisted."""
    from app.routers.audits import _process_one

    async with sqlite_db() as session:
        repo = IssueProposalRepository(session)
        await repo.create_pending(
            run_id="other-run",
            project_id="other-project",
            project_name="Other",
            repo_owner="odanree",
            repo_name="other-repo",
            proposed_title="x",
            proposed_body="y",
            proposed_labels=[],
        )

    result = await _process_one(_payload(project_id="p1"))
    assert result["status"] == "pending_approval"
    assert len(fake_slack.posted) == 1

    async with sqlite_db() as session:
        repo = IssueProposalRepository(session)
        row = await repo.get_by_run_id(result["run_id"])
        assert row is not None
        assert row.status == "pending"
        assert row.project_id == "p1"


# ── DB transitions through resume ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_approve_transitions_pending_to_created(
    sqlite_db, init_test_graph, fake_slack, fake_github
):
    """Approve → DB transitions to approved then created (with issue url+number)."""
    from app.routers.audits import _process_one
    from app.routers.slack_actions import _resume_and_log

    result = await _process_one(_payload())
    run_id = result["run_id"]

    async with sqlite_db() as session:
        row = await IssueProposalRepository(session).get_by_run_id(run_id)
        assert row.status == "pending"

    await _resume_and_log(run_id, approved=True, reviewer_id="alice")

    async with sqlite_db() as session:
        row = await IssueProposalRepository(session).get_by_run_id(run_id)
        assert row.status == "created"
        assert row.reviewer_slack_id == "alice"
        assert row.github_issue_number == 42
        assert "github.com" in (row.github_issue_url or "")


@pytest.mark.asyncio
async def test_resume_reject_transitions_pending_to_rejected(
    sqlite_db, init_test_graph, fake_slack, fake_github
):
    """Reject → DB row goes straight to rejected, no GitHub call made."""
    from app.routers.audits import _process_one
    from app.routers.slack_actions import _resume_and_log

    result = await _process_one(_payload())
    run_id = result["run_id"]

    await _resume_and_log(run_id, approved=False, reviewer_id="bob")

    async with sqlite_db() as session:
        row = await IssueProposalRepository(session).get_by_run_id(run_id)
        assert row.status == "rejected"
        assert row.reviewer_slack_id == "bob"
        assert row.github_issue_url is None
    assert fake_github.created == []


@pytest.mark.asyncio
async def test_resume_failure_marks_proposal_failed(
    sqlite_db, init_test_graph, fake_slack, fake_github, make_deps
):
    """Approve but GitHub call raises → DB row reaches failed status."""
    from app.routers.audits import _process_one
    from app.routers.slack_actions import _resume_and_log

    fake_github.raise_on_create = RuntimeError("GitHub 403 on POST …")
    make_deps(github_override=fake_github)

    result = await _process_one(_payload())
    run_id = result["run_id"]

    await _resume_and_log(run_id, approved=True, reviewer_id="carol")

    async with sqlite_db() as session:
        row = await IssueProposalRepository(session).get_by_run_id(run_id)
        assert row.status == "failed"
        assert "create_issue_failed" in (row.error or "")


@pytest.mark.asyncio
async def test_dedup_lets_re_audit_through_after_rejection(
    sqlite_db, init_test_graph, fake_slack, fake_github
):
    """Once a proposal is rejected, a follow-up audit on the same project
    should proceed (rejected is terminal, not 'open')."""
    from app.routers.audits import _process_one
    from app.routers.slack_actions import _resume_and_log

    first = await _process_one(_payload())
    assert first["status"] == "pending_approval"
    await _resume_and_log(first["run_id"], approved=False, reviewer_id="bob")

    fake_slack.posted.clear()
    fake_github.created.clear()
    second = await _process_one(_payload())
    assert second["status"] == "pending_approval"
    assert len(fake_slack.posted) == 1
