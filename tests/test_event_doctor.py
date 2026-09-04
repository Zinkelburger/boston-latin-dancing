"""The doctor consolidates release blockers without changing project data."""

from datetime import datetime, timedelta, timezone

import atomic_io
import event_doctor as doctor


NOW = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)


def _event(**overrides):
    event = {
        "id": "evt-1",
        "name": "Salsa Social",
        "startDate": "2026-09-10T20:00:00-04:00",
        "location": "Boston, MA",
        "lat": 42.36,
        "lng": -71.06,
        "source": "manual",
    }
    event.update(overrides)
    return event


def _wire(monkeypatch, tmp_path, *, active=None, pending=None, rejected=None, report=None):
    active = [_event()] if active is None else active
    monkeypatch.setattr(doctor, "load_sources", lambda: [])
    monkeypatch.setattr(doctor, "scraper_commands", lambda: [])
    monkeypatch.setattr(doctor, "load_scrape_health", lambda: {})
    monkeypatch.setattr(doctor, "load_active", lambda: active)
    monkeypatch.setattr(doctor, "load_pending", lambda: pending or [])
    monkeypatch.setattr(doctor, "load_rejected", lambda: rejected or [])
    monkeypatch.setattr(doctor, "duplicate_report", lambda events: [])
    report_path = tmp_path / "verification-report.json"
    atomic_io.write_json(report_path, report if report is not None else [{
        "event_id": "evt-1",
        "status": "confirmed",
        "verified_at": NOW.isoformat(),
    }])
    monkeypatch.setattr(doctor, "REPORT_PATH", report_path)


def test_healthy_doctor_without_publish_preview(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)

    result = doctor.run_doctor(now=NOW, include_publish_preview=False)

    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert result["summary"]["blockers"] == 0
    assert result["checks"]["coordinates"]["status"] == "ok"


def test_doctor_aggregates_queues_coordinates_verification_and_duplicates(monkeypatch, tmp_path):
    event = _event(lat=None, lng=None)
    _wire(monkeypatch, tmp_path, active=[event], pending=[_event(id="pending-1")], report=[])
    monkeypatch.setattr(doctor, "duplicate_report", lambda events: [{"a_id": "evt-1", "b_id": "evt-2"}])

    result = doctor.run_doctor(now=NOW, include_publish_preview=False)

    assert result["ok"] is False
    blocker_names = {row["check"] for row in result["blockers"]}
    assert {"pending_review", "coordinates", "verification", "active_duplicates"} <= blocker_names


def test_doctor_flags_stale_scraper_health(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "scraper_commands", lambda: [("feed", ["python", "scraper.py"])])
    monkeypatch.setattr(doctor, "load_scrape_health", lambda: {
        "feed": {
            "status": "ok",
            "last_run": (NOW - timedelta(hours=49)).isoformat(),
            "raw_found": 1,
            "kept": 1,
        }
    })

    result = doctor.run_doctor(now=NOW, include_publish_preview=False)

    assert result["checks"]["scraper_health"]["status"] == "blocker"
    assert result["checks"]["scraper_health"]["items"][0]["problem"] == "stale health"
