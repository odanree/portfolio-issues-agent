from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class IssueProposalState(TypedDict):
    # ── input ───────────────────────────────────────────────
    run_id: str
    project_id: str
    project_name: str
    repo_owner: str
    repo_name: str
    report: dict[str, Any]  # DriftReport.asdict()

    # ── classify node ───────────────────────────────────────
    classification: str | None  # "issue_worthy" | "skip"
    skip_reason: str | None

    # ── draft node ──────────────────────────────────────────
    proposed_title: str | None
    proposed_body: str | None
    proposed_labels: list[str]

    # ── notify_slack node ───────────────────────────────────
    slack_message_ts: str | None
    slack_channel: str | None

    # ── HITL gate (set via aupdate_state) ───────────────────
    approval_granted: bool | None
    approved_by: str | None
    decided_at: str | None

    # ── create_issue node ───────────────────────────────────
    issue_url: str | None
    issue_number: int | None

    # ── bookkeeping ─────────────────────────────────────────
    error: str | None
    messages: Annotated[list, add_messages]
