import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IssueProposal(Base):
    """Audit log of every drift finding the agent has surfaced.

    One row per LangGraph run. Status moves through:
        pending → approved | rejected → created | failed
    `created` means the GitHub issue was successfully opened.
    """

    __tablename__ = "issue_proposals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    run_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    proposed_title: Mapped[str] = mapped_column(String(300), nullable=False)
    proposed_body: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_labels: Mapped[str] = mapped_column(Text, default="")  # comma-joined
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reviewer_slack_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    github_issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<IssueProposal run_id={self.run_id} project={self.project_name!r} "
            f"status={self.status}>"
        )
