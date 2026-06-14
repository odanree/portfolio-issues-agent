"""Slack request-signature verification — HMAC-SHA256 + replay window."""
import hashlib
import hmac
import time

from app.services.slack_client import verify_signature

SECRET = "test-signing-secret"


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_accepted():
    body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert verify_signature(body, ts, sig, SECRET) is True


def test_tampered_body_rejected():
    body = b"payload=original"
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    tampered = b"payload=evil"
    assert verify_signature(tampered, ts, sig, SECRET) is False


def test_replay_outside_window_rejected():
    body = b"payload=anything"
    ts = str(int(time.time()) - 600)  # 10 min old
    sig = _sign(body, ts)
    assert verify_signature(body, ts, sig, SECRET, max_age_s=300) is False


def test_missing_secret_rejects_everything():
    body = b"payload=x"
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert verify_signature(body, ts, sig, "") is False


def test_invalid_timestamp_rejected():
    body = b"payload=x"
    sig = _sign(body, "not-a-number")
    assert verify_signature(body, "not-a-number", sig, SECRET) is False
