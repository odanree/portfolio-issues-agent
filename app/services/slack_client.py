"""Slack Block Kit poster.

Uses chat.postMessage with a Bot Token so we can:
  - target a channel by id
  - capture the posted message ts to reply in a thread later
  - support the interactive approve/reject buttons (incoming webhooks don't)
"""
from __future__ import annotations

import hashlib
import hmac
import time

import httpx
import structlog

log = structlog.get_logger()

SLACK_API = "https://slack.com/api"


def verify_signature(
    body: bytes, timestamp: str, signature: str, signing_secret: str, max_age_s: int = 300
) -> bool:
    """Verify a Slack interactive payload against the signing secret.

    Rejects replays older than max_age_s (default 5 min, Slack's stated window).
    """
    if not signing_secret:
        return False
    try:
        if abs(time.time() - int(timestamp)) > max_age_s:
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_approval_blocks(
    *,
    run_id: str,
    project_name: str,
    repo_owner: str,
    repo_name: str,
    proposed_title: str,
    proposed_body: str,
    labels: list[str],
) -> list[dict]:
    """Build the Block Kit blocks for an approval prompt."""
    body_excerpt = proposed_body if len(proposed_body) <= 1000 else proposed_body[:1000] + "…"
    labels_str = ", ".join(labels) if labels else "(none)"
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Portfolio drift issue proposal — {project_name}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repo*\n`{repo_owner}/{repo_name}`"},
                {"type": "mrkdwn", "text": f"*Labels*\n{labels_str}"},
                {"type": "mrkdwn", "text": f"*Run ID*\n`{run_id}`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Title*\n{proposed_title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Body*\n```{body_excerpt}```"},
        },
        {
            "type": "actions",
            "block_id": "issue_approval_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "approve_issue",
                    "value": f"run_id:{run_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_issue",
                    "value": f"run_id:{run_id}",
                },
            ],
        },
    ]


class SlackClient:
    """Thin async wrapper around chat.postMessage."""

    def __init__(self, bot_token: str, *, base_url: str = SLACK_API) -> None:
        self._bot_token = bot_token
        self._base_url = base_url
        self._client = httpx.AsyncClient(timeout=10.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        thread_ts: str | None = None,
    ) -> dict:
        """POST chat.postMessage. Returns the parsed JSON response."""
        if not self._bot_token:
            log.warning("slack_bot_token_not_configured")
            return {"ok": False, "error": "missing_bot_token"}
        payload: dict = {"channel": channel, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        if thread_ts is not None:
            payload["thread_ts"] = thread_ts
        try:
            r = await self._client.post(
                f"{self._base_url}/chat.postMessage",
                headers=self._headers(),
                json=payload,
            )
            data = r.json()
            if not data.get("ok"):
                log.warning("slack_post_message_failed", error=data.get("error"))
            return data
        except httpx.HTTPError as e:
            log.error("slack_post_message_http_error", error=str(e))
            return {"ok": False, "error": str(e)}

    async def post_approval_request(
        self,
        *,
        channel: str,
        run_id: str,
        project_name: str,
        repo_owner: str,
        repo_name: str,
        proposed_title: str,
        proposed_body: str,
        labels: list[str],
    ) -> dict:
        """Post the Block Kit approve/reject prompt."""
        blocks = build_approval_blocks(
            run_id=run_id,
            project_name=project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
            proposed_title=proposed_title,
            proposed_body=proposed_body,
            labels=labels,
        )
        return await self.post_message(
            channel=channel,
            text=f"Drift issue proposal for {repo_owner}/{repo_name}: {proposed_title}",
            blocks=blocks,
        )

    async def post_result(
        self,
        *,
        channel: str,
        run_id: str,
        approved: bool,
        reviewer: str,
        issue_url: str | None = None,
        thread_ts: str | None = None,
        error: str | None = None,
    ) -> dict:
        """Post the result (created / rejected / failed) — threaded if ts known."""
        if approved and issue_url:
            text = f":white_check_mark: Approved by `{reviewer}` → opened {issue_url}"
        elif approved and error:
            text = f":x: Approved by `{reviewer}` but issue creation failed: {error[:200]}"
        elif approved:
            text = f":hourglass: Approved by `{reviewer}` (run `{run_id}`)"
        else:
            text = f":no_entry: Rejected by `{reviewer}` (run `{run_id}`)"
        return await self.post_message(channel=channel, text=text, thread_ts=thread_ts)

    async def close(self) -> None:
        await self._client.aclose()
