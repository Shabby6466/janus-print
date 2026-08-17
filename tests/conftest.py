from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Every test gets its own SQLite database, archive directory, and clean caches."""
    monkeypatch.setenv("JANUS_PRINT_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("JANUS_PRINT_ARCHIVE_BACKEND", "fs")
    monkeypatch.setenv("JANUS_PRINT_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("JANUS_PRINT_RULES_DIR", str(REPO_ROOT / "rules"))
    monkeypatch.setenv("JANUS_PRINT_PRINTERS_CONFIG", str(REPO_ROOT / "config" / "printers.yaml"))
    monkeypatch.setenv("JANUS_PRINT_DEV_MODE", "true")
    monkeypatch.setenv("JANUS_PRINT_ARCHIVE_MASTER_KEY", "test-master-key")
    monkeypatch.setenv("JANUS_PRINT_SIEM_ENABLED", "false")
    monkeypatch.setenv("JANUS_PRINT_REDIS_URL", "redis://127.0.0.1:1/0")  # unreachable on purpose

    from janusprint import config, db
    from janusprint.archive import store
    from janusprint.bridge import cef
    from janusprint.inspector import engine

    from janusprint.inspector import store as rule_store

    config.reset_caches()
    db.reset_engine()
    store.reset_archive()
    cef.reset_bridge()
    rule_store.invalidate_cache()

    db.init_db()
    # Rules live in the database now; seed the shipped packs so tests exercise the same
    # ruleset the product ships with.
    from janusprint import printers as printer_store
    from janusprint.models import PrinterQueue

    with db.session_scope() as seeding:
        rule_store.seed_from_yaml(seeding)
        # Queue policies the tests exercise. Defined here rather than relying on the
        # shipped config/printers.yaml, which ships no example queues — a test suite that
        # depends on sample configuration breaks the moment the samples change.
        seeding.add(
            PrinterQueue(
                name="finance-laser",
                device_uri="",
                deep_scan_required=True,
                fail_mode="closed",
                on_unreadable="hold",
                rule_tags=["*"],
                cups_state="external",
                description="strict queue fixture",
            )
        )
        seeding.add(
            PrinterQueue(
                name="office-laser",
                device_uri="",
                deep_scan_required=False,
                fail_mode="open",
                on_unreadable="log",
                rule_tags=["*"],
                cups_state="external",
                description="permissive queue fixture",
            )
        )
    rule_store.invalidate_cache()
    printer_store.invalidate_cache()
    yield

    config.reset_caches()
    db.reset_engine()
    store.reset_archive()
    cef.reset_bridge()
    rule_store.invalidate_cache()

    from janusprint import printers as printer_store

    printer_store.invalidate_cache()


@pytest.fixture
def session():
    from janusprint.db import get_sessionmaker

    with get_sessionmaker()() as session:
        yield session
        session.commit()


def make_pdf(lines: list[str], path: Path | None = None) -> bytes:
    """Build a small text-layer PDF. Requires reportlab (dev dependency)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    target = path or Path(tempfile.mkstemp(suffix=".pdf")[1])
    pdf = canvas.Canvas(str(target), pagesize=A4)
    y = 780
    for line in lines:
        if y < 60:
            pdf.showPage()
            y = 780
        pdf.drawString(60, y, line)
        y -= 16
    pdf.save()
    data = target.read_bytes()
    if path is None:
        os.unlink(target)
    return data


@pytest.fixture
def pdf_factory():
    return make_pdf


@pytest.fixture
def client():
    """FastAPI test client with an admin session already established."""
    from fastapi.testclient import TestClient

    from janusprint.api.app import create_app
    from janusprint.api.auth import ensure_admin_user
    from janusprint.db import session_scope

    with session_scope() as scoped:
        ensure_admin_user(scoped, "admin", "test-password")

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def authed_client(client):
    response = client.post(
        "/login", data={"username": "admin", "password": "test-password", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client
