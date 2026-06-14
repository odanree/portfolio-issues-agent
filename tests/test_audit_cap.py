"""Cap and drift-density ranking inside run_weekly_audit.

We monkeypatch audit_portfolio so the test doesn't hit Beacon/GitHub/Anthropic
and inject a stub _process_one that records what reached it.
"""
from __future__ import annotations

import pytest

from app.routers import audits


def _finding(project_id: str, *, density: dict) -> dict:
    """Build a finding payload with a given drift density."""
    return {
        "project_id": project_id,
        "project_name": project_id,
        "repo_owner": "odanree",
        "repo_name": project_id,
        "report": {
            "project_id": project_id,
            "project_name": project_id,
            "is_stale": density.get("is_stale", False),
            "has_changes": True,
            "description_suggestion": "x" if density.get("description") else None,
            "outcome_suggestion": "x" if density.get("outcome") else None,
            "tech_stack_additions": ["A"] * density.get("additions", 0),
            "tech_stack_removals": ["B"] * density.get("removals", 0),
            "canonical_url": None,
            "notes": "",
        },
    }


def test_drift_density_scores_match_intent():
    """The score function should produce the expected ordering."""
    empty = _finding("low", density={})
    rich = _finding(
        "high",
        density={
            "description": True,
            "outcome": True,
            "additions": 3,
            "removals": 2,
            "is_stale": True,
        },
    )
    assert audits._drift_density(empty) == 0
    assert audits._drift_density(rich) == 1 + 1 + 3 + 2 + 2  # =9


@pytest.mark.asyncio
async def test_audit_caps_at_configured_limit(monkeypatch):
    """8 findings with a cap of 3 → only 3 are processed."""
    findings = [
        _finding(f"p{i}", density={"additions": i}) for i in range(8)
    ]
    monkeypatch.setattr(
        "app.services.drift.audit_portfolio", lambda model: iter(findings)
    )

    seen: list[str] = []

    async def _stub_process_one(payload):
        seen.append(payload["project_id"])
        return {"status": "pending_approval", "run_id": payload["project_id"]}

    monkeypatch.setattr(audits, "_process_one", _stub_process_one)

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PROPOSALS_PER_RUN", "3")

    result = await audits.run_weekly_audit()

    assert result["total_found"] == 8
    assert result["processed"] == 3
    assert result["dropped_by_cap"] == 5
    # The 3 kept are the highest-density (p7, p6, p5 → 7, 6, 5 additions)
    assert seen == ["p7", "p6", "p5"]


@pytest.mark.asyncio
async def test_audit_no_cap_when_under_limit(monkeypatch):
    """Findings count under the cap → all processed in original order."""
    findings = [_finding(f"p{i}", density={"additions": 1}) for i in range(3)]
    monkeypatch.setattr(
        "app.services.drift.audit_portfolio", lambda model: iter(findings)
    )

    seen: list[str] = []

    async def _stub_process_one(payload):
        seen.append(payload["project_id"])
        return {"status": "pending_approval", "run_id": payload["project_id"]}

    monkeypatch.setattr(audits, "_process_one", _stub_process_one)

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PROPOSALS_PER_RUN", "5")

    result = await audits.run_weekly_audit()

    assert result["total_found"] == 3
    assert result["processed"] == 3
    assert result["dropped_by_cap"] == 0
    # Order preserved when no cap applied
    assert seen == ["p0", "p1", "p2"]


@pytest.mark.asyncio
async def test_audit_cap_zero_disables_capping(monkeypatch):
    """max_proposals_per_run=0 means 'no cap'."""
    findings = [_finding(f"p{i}", density={"additions": 1}) for i in range(8)]
    monkeypatch.setattr(
        "app.services.drift.audit_portfolio", lambda model: iter(findings)
    )

    seen: list[str] = []

    async def _stub_process_one(payload):
        seen.append(payload["project_id"])
        return {"status": "pending_approval", "run_id": payload["project_id"]}

    monkeypatch.setattr(audits, "_process_one", _stub_process_one)

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PROPOSALS_PER_RUN", "0")

    result = await audits.run_weekly_audit()

    assert result["total_found"] == 8
    assert result["processed"] == 8
    assert result["dropped_by_cap"] == 0
    assert len(seen) == 8
