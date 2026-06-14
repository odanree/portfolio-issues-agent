"""End-to-end graph behavior with MemorySaver and stub clients."""
import pytest

from app.agent.graph import get_current_state, resume_workflow, start_workflow

REPORT_WITH_DRIFT = {
    "project_id": "p1",
    "project_name": "Test Project",
    "is_stale": False,
    "has_changes": True,
    "description_suggestion": "Mention CLIP.",
    "outcome_suggestion": None,
    "tech_stack_additions": ["Rust"],
    "tech_stack_removals": [],
    "canonical_url": None,
    "notes": "tech drift",
}

REPORT_NO_DRIFT = {**REPORT_WITH_DRIFT, "has_changes": False}


def _initial_state(run_id: str = "run-1", report: dict | None = None) -> dict:
    return {
        "run_id": run_id,
        "project_id": "p1",
        "project_name": "Test Project",
        "repo_owner": "odanree",
        "repo_name": "test-repo",
        "report": report or REPORT_WITH_DRIFT,
        "proposed_labels": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_start_workflow_runs_until_interrupt(init_test_graph, fake_slack, fake_github):
    """Graph hits the interrupt before create_issue. No GitHub call made."""
    run_id = await start_workflow(_initial_state())
    snap = await get_current_state(run_id)
    assert snap is not None
    assert snap["classification"] == "issue_worthy"
    assert snap["proposed_title"]  # draft node ran
    assert snap["proposed_body"]
    assert snap.get("issue_url") is None
    assert len(fake_slack.posted) == 1  # approval prompt posted
    assert fake_github.created == []   # nothing created yet


@pytest.mark.asyncio
async def test_classify_skips_graph_on_no_drift(init_test_graph, fake_slack, fake_github):
    """has_changes=False → END at classify, no Slack message, no GitHub call."""
    run_id = await start_workflow(_initial_state(run_id="r-skip", report=REPORT_NO_DRIFT))
    snap = await get_current_state(run_id)
    assert snap["classification"] == "skip"
    assert fake_slack.posted == []
    assert fake_github.created == []


@pytest.mark.asyncio
async def test_resume_approve_creates_issue(init_test_graph, fake_slack, fake_github):
    """Approve path: aupdate_state + resume → GitHub POST runs."""
    run_id = await start_workflow(_initial_state(run_id="r-approve"))
    final = await resume_workflow(run_id, approved=True, reviewer_id="alice")
    assert final.get("issue_url", "").startswith("https://github.com/")
    assert final.get("issue_number") == 42
    assert len(fake_github.created) == 1
    assert fake_github.created[0]["owner"] == "odanree"
    # post_result was posted with approved=True
    assert len(fake_slack.results) == 1
    assert fake_slack.results[0]["approved"] is True
    assert fake_slack.results[0]["issue_url"].startswith("https://github.com/")


@pytest.mark.asyncio
async def test_resume_reject_skips_creation(init_test_graph, fake_slack, fake_github):
    """Reject path: graph resumes but create_issue makes no GitHub call."""
    run_id = await start_workflow(_initial_state(run_id="r-reject"))
    final = await resume_workflow(run_id, approved=False, reviewer_id="bob")
    assert final.get("issue_url") is None
    assert final.get("issue_number") is None
    assert fake_github.created == []
    # post_result still ran
    assert len(fake_slack.results) == 1
    assert fake_slack.results[0]["approved"] is False


@pytest.mark.asyncio
async def test_draft_node_parses_json_with_fences(init_test_graph, fake_github, make_deps):
    """Claude sometimes wraps JSON in fences — the parser must handle it."""
    from tests.conftest import FakeAnthropic

    fenced = """```json
{
  "title": "Update README to mention Rust",
  "body": "- mention rust in README\\n- update tech_stack",
  "labels": ["documentation"]
}
```"""
    deps = make_deps(anthropic_override=FakeAnthropic(response_text=fenced))

    # Re-init graph so the new deps are picked up (deps is module-level; init_test_graph
    # already injected the default. We re-injected via make_deps, and graph nodes look
    # up _deps each call, so no rebuild needed.)
    run_id = await start_workflow(_initial_state(run_id="r-fence"))
    snap = await get_current_state(run_id)
    assert snap["proposed_title"] == "Update README to mention Rust"
    assert "rust" in snap["proposed_body"].lower()
    assert snap["proposed_labels"] == ["documentation"]


@pytest.mark.asyncio
async def test_create_issue_error_recorded_in_state(
    init_test_graph, fake_slack, fake_github, make_deps
):
    """If GitHub raises, state.error is set and post_result still runs."""
    fake_github.raise_on_create = RuntimeError("GitHub 403 on POST …")
    make_deps(github_override=fake_github)
    run_id = await start_workflow(_initial_state(run_id="r-err"))
    final = await resume_workflow(run_id, approved=True, reviewer_id="carol")
    assert "create_issue_failed" in (final.get("error") or "")
    assert final.get("issue_url") is None
    assert len(fake_slack.results) == 1
