"""Audit trigger.

Two entry points:
- POST /api/audits/run  → operator manual kick (also what APScheduler calls)
- run_weekly_audit()    → in-process function the scheduler invokes directly
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog
from fastapi import APIRouter

from app.agent.graph import start_workflow
from app.config import get_settings
from app.db.repository import IssueProposalRepository
from app.db.session import AsyncSessionLocal

log = structlog.get_logger()
router = APIRouter()


async def _process_one(payload: dict[str, Any]) -> dict:
    """Persist a pending row, then drive the graph to its interrupt."""
    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "project_id": payload["project_id"],
        "project_name": payload["project_name"],
        "repo_owner": payload["repo_owner"],
        "repo_name": payload["repo_name"],
        "report": payload["report"],
        "proposed_labels": [],
        "messages": [],
    }
    await start_workflow(initial_state)

    # Persist after the graph has drafted; pull current state to capture draft fields
    from app.agent.graph import get_current_state
    current = await get_current_state(run_id) or {}
    title = current.get("proposed_title") or "(empty)"
    body = current.get("proposed_body") or ""
    labels = current.get("proposed_labels") or []
    classification = current.get("classification")

    if classification == "skip":
        log.info(
            "audit_skipped_project",
            run_id=run_id,
            project=payload["project_name"],
            reason=current.get("skip_reason"),
        )
        return {"run_id": run_id, "status": "skipped"}

    if AsyncSessionLocal is not None:
        async with AsyncSessionLocal() as session:
            repo = IssueProposalRepository(session)
            existing_open = await repo.list_open_proposals_for_project(payload["project_id"])
            if existing_open:
                log.info(
                    "audit_skip_existing_open_proposal",
                    project=payload["project_name"],
                    open_run_ids=[p.run_id for p in existing_open],
                )
                return {"run_id": run_id, "status": "skipped_existing"}
            await repo.create_pending(
                run_id=run_id,
                project_id=payload["project_id"],
                project_name=payload["project_name"],
                repo_owner=payload["repo_owner"],
                repo_name=payload["repo_name"],
                proposed_title=title,
                proposed_body=body,
                proposed_labels=labels,
            )
    return {"run_id": run_id, "status": "pending_approval"}


async def run_weekly_audit() -> dict:
    """Pull drift findings from the upstream analyzer and kick a workflow per finding."""
    settings = get_settings()

    # The drift wrapper is sync (httpx.Client + Anthropic SDK is sync there).
    # Run it in a thread so we don't block the event loop.
    from app.services.drift import audit_portfolio

    loop_results = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: list(audit_portfolio(model=settings.agent_model)))
        findings = future.result()

    log.info("audit_findings_collected", count=len(findings))

    processed = []
    for payload in findings:
        try:
            result = await _process_one(payload)
            processed.append({**result, "project": payload["project_name"]})
        except Exception as e:
            log.exception(
                "audit_process_one_failed", project=payload["project_name"], error=str(e)
            )
            processed.append({"project": payload["project_name"], "status": "error", "error": str(e)})
    return {"total": len(findings), "results": processed}


@router.post("/api/audits/run")
async def trigger_audit() -> dict:
    return await run_weekly_audit()
