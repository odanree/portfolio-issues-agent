from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IssueProposal


class IssueProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(
        self,
        *,
        run_id: str,
        project_id: str,
        project_name: str,
        repo_owner: str,
        repo_name: str,
        proposed_title: str,
        proposed_body: str,
        proposed_labels: list[str],
    ) -> IssueProposal:
        row = IssueProposal(
            run_id=run_id,
            project_id=project_id,
            project_name=project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
            proposed_title=proposed_title,
            proposed_body=proposed_body,
            proposed_labels=",".join(proposed_labels),
            status="pending",
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def get_by_run_id(self, run_id: str) -> IssueProposal | None:
        stmt = select(IssueProposal).where(IssueProposal.run_id == run_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def mark_decided(
        self, run_id: str, *, approved: bool, reviewer_slack_id: str
    ) -> IssueProposal | None:
        row = await self.get_by_run_id(run_id)
        if row is None:
            return None
        row.status = "approved" if approved else "rejected"
        row.reviewer_slack_id = reviewer_slack_id
        row.decided_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def mark_created(
        self, run_id: str, *, issue_url: str, issue_number: int
    ) -> IssueProposal | None:
        row = await self.get_by_run_id(run_id)
        if row is None:
            return None
        row.status = "created"
        row.github_issue_url = issue_url
        row.github_issue_number = issue_number
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def mark_failed(self, run_id: str, *, error: str) -> IssueProposal | None:
        row = await self.get_by_run_id(run_id)
        if row is None:
            return None
        row.status = "failed"
        row.error = error[:2000]
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list_open_proposals_for_project(
        self, project_id: str
    ) -> list[IssueProposal]:
        """Return all pending/approved (i.e. not-yet-terminal) proposals for a project.

        Used by the audit trigger for idempotency: if there's already an open
        proposal for a project, skip surfacing the same drift again this week.
        """
        stmt = select(IssueProposal).where(
            IssueProposal.project_id == project_id,
            IssueProposal.status.in_(("pending", "approved")),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
