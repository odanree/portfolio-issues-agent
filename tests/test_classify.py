"""Unit tests for the classify node — gates the rest of the graph."""
import pytest

from app.agent.nodes import classify


def _state(report: dict) -> dict:
    return {
        "run_id": "r1",
        "project_id": "p1",
        "project_name": "x",
        "repo_owner": "o",
        "repo_name": "r",
        "report": report,
        "proposed_labels": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_classify_skips_when_no_drift():
    out = await classify(_state({"has_changes": False}))
    assert out["classification"] == "skip"
    assert out["skip_reason"] == "no_drift"


@pytest.mark.asyncio
async def test_classify_skips_when_suggestions_empty():
    report = {
        "has_changes": True,
        "description_suggestion": None,
        "outcome_suggestion": None,
        "tech_stack_additions": [],
        "tech_stack_removals": [],
    }
    out = await classify(_state(report))
    assert out["classification"] == "skip"
    assert out["skip_reason"] == "empty_suggestion"


@pytest.mark.asyncio
async def test_classify_proceeds_on_tech_addition():
    report = {
        "has_changes": True,
        "description_suggestion": None,
        "outcome_suggestion": None,
        "tech_stack_additions": ["Rust"],
        "tech_stack_removals": [],
    }
    out = await classify(_state(report))
    assert out["classification"] == "issue_worthy"


@pytest.mark.asyncio
async def test_classify_proceeds_on_description_suggestion():
    report = {
        "has_changes": True,
        "description_suggestion": "Mention CLIP embeddings",
        "outcome_suggestion": None,
        "tech_stack_additions": [],
        "tech_stack_removals": [],
    }
    out = await classify(_state(report))
    assert out["classification"] == "issue_worthy"
