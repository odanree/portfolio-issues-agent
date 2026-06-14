"""Thin wrapper that drives portfolio-drift-agent's analyzer over the Beacon
portfolio and yields DriftReport rows for projects that drifted.

We DO NOT reimplement the analyzer or the Beacon adapter — those live in the
upstream package and we depend on them.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterator

import structlog

log = structlog.get_logger()


def _is_issue_worthy(report) -> bool:
    """Filter: only escalate when there's at least one substantive change.

    `has_changes` covers description/outcome/tech_stack drift. Empty proposal
    rows (all suggestions None, no tech additions/removals) are dropped here
    so we don't post no-op Slack prompts.
    """
    if not getattr(report, "has_changes", False):
        return False
    has_text = bool(
        getattr(report, "description_suggestion", None)
        or getattr(report, "outcome_suggestion", None)
    )
    has_tech = bool(
        getattr(report, "tech_stack_additions", None)
        or getattr(report, "tech_stack_removals", None)
    )
    return has_text or has_tech


def audit_portfolio(model: str) -> Iterator[dict]:
    """Run the drift audit against the live Beacon portfolio.

    Yields dicts: {project, snapshot, report} so the agent layer can persist
    everything it needs without re-fetching. Beacon + GitHub + Anthropic are
    all called by the upstream analyzer; this wrapper just enforces the
    issue-worthiness predicate and yields.
    """
    from anthropic import Anthropic
    from drift_agent.adapters import BeaconAdapter
    from drift_agent.analyzer import analyze
    from drift_agent.github import build_client, fetch_snapshot, parse_owner_repo

    portfolio = BeaconAdapter()
    projects = portfolio.fetch_projects()
    gh = build_client()
    client = Anthropic()

    for project in projects:
        if not project.url:
            continue
        parsed = parse_owner_repo(project.url)
        if not parsed:
            continue
        owner, repo = parsed
        snap = fetch_snapshot(gh, owner, repo)
        if snap is None:
            log.warning(
                "drift_skip_missing_snapshot", project=project.name, repo=f"{owner}/{repo}"
            )
            continue
        report = analyze(client, project, snap, model=model)
        if not _is_issue_worthy(report):
            log.debug("drift_skip_not_issue_worthy", project=project.name)
            continue
        yield {
            "project_id": project.id,
            "project_name": project.name,
            "repo_owner": owner,
            "repo_name": repo,
            "report": asdict(report),
        }
