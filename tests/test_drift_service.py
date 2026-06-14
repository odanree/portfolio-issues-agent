"""Regression: serialize_report must include `has_changes` in the dict it
returns. asdict() drops @property fields, so the graph's classify node was
seeing has_changes as missing and skipping every project."""
from dataclasses import asdict

from drift_agent.models import DriftReport

from app.services.drift import serialize_report


def test_dataclass_asdict_drops_has_changes_property():
    """Anchor test: confirms the underlying drop that motivated the fix."""
    report = DriftReport(
        project_id="p",
        project_name="x",
        is_stale=False,
        description_suggestion="add CLIP",
    )
    assert report.has_changes is True
    plain = asdict(report)
    assert "has_changes" not in plain


def test_serialize_report_includes_has_changes():
    """serialize_report wraps asdict and re-adds the @property."""
    report = DriftReport(
        project_id="p1",
        project_name="Test",
        is_stale=False,
        description_suggestion="add CLIP",
        tech_stack_additions=["Rust"],
    )
    out = serialize_report(report)
    assert out["has_changes"] is True
    assert out["description_suggestion"] == "add CLIP"
    assert out["tech_stack_additions"] == ["Rust"]


def test_serialize_report_has_changes_false_when_empty():
    report = DriftReport(project_id="p", project_name="x", is_stale=False)
    out = serialize_report(report)
    assert out["has_changes"] is False
