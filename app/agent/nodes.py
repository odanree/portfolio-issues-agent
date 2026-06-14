"""LangGraph node implementations.

External dependencies (Anthropic client, GitHub client, Slack client, DB
session factory) are injected once during app startup via inject_deps() so
the nodes themselves are pure callables that the graph can drive.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import structlog

from app.agent.state import IssueProposalState

log = structlog.get_logger()

DRAFT_PROMPT = """\
You are drafting a GitHub issue to propose updating a project repo so its
README/docs match what the code actually does. The drift findings below were
detected by comparing the live GitHub repo against the maintainer's portfolio
profile.

Project: {project_name}
Repo: {repo_owner}/{repo_name}
Drift summary (JSON):
{report_json}

Write a focused, actionable issue. Required JSON output shape:
{{
  "title": "string, <= 80 chars, imperative voice (e.g. 'Update README to mention …')",
  "body": "markdown, 4-15 lines, with concrete bullet points referencing what changed and what to update; do NOT instruct the reader to edit a portfolio/resume — this issue lives on the repo, talk in terms of README/docs",
  "labels": ["documentation"]
}}

Constraints:
- Output ONLY the JSON object, no surrounding prose.
- Do not invent facts not present in the drift summary.
- If suggestions are empty, return an empty title + body and labels=[].
"""


@dataclass
class AgentDeps:
    """Bundle of services the graph nodes need."""

    anthropic: Any  # anthropic.Anthropic
    model: str
    github: Any  # GitHubClient
    slack: Any  # SlackClient
    slack_channel: str
    db_session_factory: Callable[[], Any]  # returns an async context manager


_deps: AgentDeps | None = None


def inject_deps(deps: AgentDeps) -> None:
    global _deps
    _deps = deps


def _require_deps() -> AgentDeps:
    if _deps is None:
        raise RuntimeError("AgentDeps not injected. Call inject_deps() during startup.")
    return _deps


# ── classify ────────────────────────────────────────────────────────────────


async def classify(state: IssueProposalState) -> dict:
    """Gate: only proceed if the report actually has changes worth surfacing."""
    report = state.get("report") or {}
    has_changes = bool(report.get("has_changes"))
    if not has_changes:
        return {"classification": "skip", "skip_reason": "no_drift"}
    if not (
        report.get("description_suggestion")
        or report.get("outcome_suggestion")
        or report.get("tech_stack_additions")
        or report.get("tech_stack_removals")
    ):
        return {"classification": "skip", "skip_reason": "empty_suggestion"}
    return {"classification": "issue_worthy"}


# ── draft ───────────────────────────────────────────────────────────────────


def _parse_json_safely(text: str) -> dict:
    """Find the first {...} block and parse it. Lenient against code fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip first fence + optional language tag
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in: {text[:200]}")
    return json.loads(text[start : end + 1])


async def draft(state: IssueProposalState) -> dict:
    """Ask Claude to draft the issue title + body + labels."""
    deps = _require_deps()
    report = state["report"]
    prompt = DRAFT_PROMPT.format(
        project_name=state["project_name"],
        repo_owner=state["repo_owner"],
        repo_name=state["repo_name"],
        report_json=json.dumps(report, default=str, indent=2),
    )
    resp = deps.anthropic.messages.create(
        model=deps.model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    # SDK returns list of content blocks; first is usually text
    raw_text = resp.content[0].text if hasattr(resp.content[0], "text") else str(resp.content[0])
    try:
        parsed = _parse_json_safely(raw_text)
    except (ValueError, json.JSONDecodeError) as e:
        log.error("draft_parse_failed", error=str(e), raw=raw_text[:300])
        return {
            "error": f"draft_parse_failed: {e}",
            "proposed_title": None,
            "proposed_body": None,
            "proposed_labels": [],
        }
    title = str(parsed.get("title") or "").strip()
    body = str(parsed.get("body") or "").strip()
    labels = [str(x) for x in (parsed.get("labels") or [])]
    if not title or not body:
        return {
            "error": "draft_empty",
            "proposed_title": title or None,
            "proposed_body": body or None,
            "proposed_labels": labels,
        }
    return {
        "proposed_title": title,
        "proposed_body": body,
        "proposed_labels": labels,
    }


# ── notify_slack ────────────────────────────────────────────────────────────


async def notify_slack(state: IssueProposalState) -> dict:
    """Post the approve/reject Block Kit prompt to Slack."""
    deps = _require_deps()
    resp = await deps.slack.post_approval_request(
        channel=deps.slack_channel,
        run_id=state["run_id"],
        project_name=state["project_name"],
        repo_owner=state["repo_owner"],
        repo_name=state["repo_name"],
        proposed_title=state.get("proposed_title") or "",
        proposed_body=state.get("proposed_body") or "",
        labels=state.get("proposed_labels") or [],
    )
    ts = resp.get("ts") if isinstance(resp, dict) else None
    return {"slack_message_ts": ts, "slack_channel": deps.slack_channel}


# ── create_issue ────────────────────────────────────────────────────────────


async def create_issue(state: IssueProposalState) -> dict:
    """POST the GitHub issue if the human approved.

    Runs AFTER the interrupt — by which point approval_granted is set via
    aupdate_state() in resume_workflow.
    """
    if not state.get("approval_granted"):
        # rejected branch: nothing to create, post_result will handle the message
        return {"issue_url": None, "issue_number": None}
    deps = _require_deps()
    try:
        result = await deps.github.create_issue(
            owner=state["repo_owner"],
            repo=state["repo_name"],
            title=state["proposed_title"] or "",
            body=state["proposed_body"] or "",
            labels=state.get("proposed_labels") or None,
        )
    except Exception as e:
        log.error("create_issue_failed", error=str(e), run_id=state["run_id"])
        return {"error": f"create_issue_failed: {e}", "issue_url": None, "issue_number": None}
    return {
        "issue_url": result.get("html_url"),
        "issue_number": result.get("number"),
    }


# ── post_result ─────────────────────────────────────────────────────────────


async def post_result(state: IssueProposalState) -> dict:
    """Reply to the original Slack message with the outcome."""
    deps = _require_deps()
    await deps.slack.post_result(
        channel=state.get("slack_channel") or deps.slack_channel,
        run_id=state["run_id"],
        approved=bool(state.get("approval_granted")),
        reviewer=state.get("approved_by") or "unknown",
        issue_url=state.get("issue_url"),
        thread_ts=state.get("slack_message_ts"),
        error=state.get("error"),
    )
    return {"decided_at": datetime.utcnow().isoformat()}
