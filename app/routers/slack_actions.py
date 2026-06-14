"""Slack interactive action handler.

Slack delivers button clicks to POST /api/slack/actions as URL-encoded form
data with a `payload` field. We verify the signature, parse the action, and
launch resume_workflow in the background — returning 200 within Slack's
3-second window.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import structlog
from fastapi import APIRouter, HTTPException, Request, Response

from app.agent.graph import resume_workflow
from app.config import get_settings
from app.services.slack_client import verify_signature

log = structlog.get_logger()
router = APIRouter()


@router.post("/api/slack/actions")
async def handle_slack_action(request: Request) -> Response:
    settings = get_settings()

    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if settings.slack_signing_secret:
        if not verify_signature(body, timestamp, signature, settings.slack_signing_secret):
            log.warning("slack_action_invalid_signature")
            raise HTTPException(status_code=403, detail="Invalid Slack signature")

    form = parse_qs(body.decode("utf-8"))
    payload_raw = form.get("payload", [None])[0]
    if not payload_raw:
        raise HTTPException(status_code=400, detail="Missing payload field")
    try:
        data = json.loads(payload_raw)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid payload JSON")

    if data.get("type") != "block_actions":
        return Response(status_code=200)

    actions = data.get("actions") or []
    if not actions:
        return Response(status_code=200)

    action = actions[0]
    action_id = action.get("action_id") or ""
    value = action.get("value") or ""
    if not value.startswith("run_id:"):
        log.warning("slack_action_unrecognized_value", value=value, action_id=action_id)
        return Response(status_code=200)
    run_id = value[len("run_id:"):]
    approved = action_id == "approve_issue"

    user_info = data.get("user") or {}
    reviewer_id = user_info.get("name") or user_info.get("id") or "slack_user"

    log.info(
        "slack_approval_received",
        run_id=run_id, approved=approved, reviewer_id=reviewer_id,
    )

    asyncio.create_task(_resume_and_log(run_id, approved, reviewer_id))

    decision = "Approved" if approved else "Rejected"
    return Response(
        content=json.dumps(
            {"response_type": "ephemeral", "text": f"Decision recorded: *{decision}* (run `{run_id}`)."}
        ),
        media_type="application/json",
    )


async def _resume_and_log(run_id: str, approved: bool, reviewer_id: str) -> None:
    try:
        await resume_workflow(run_id, approved=approved, reviewer_id=reviewer_id)
        log.info("slack_action_workflow_resumed", run_id=run_id, approved=approved)
    except Exception as e:
        log.error("slack_action_resume_failed", run_id=run_id, error=str(e))
