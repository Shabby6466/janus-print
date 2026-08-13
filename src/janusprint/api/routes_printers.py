"""Printer queue management.

Creating a queue here reconfigures the actual print server, so every endpoint is
admin-only and every change is recorded in PrinterRevision.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import printers as printer_store
from ..db import get_session
from ..models import PrinterQueue, User
from . import cups_control
from .auth import current_user, require_role

router = APIRouter(prefix="/printers", tags=["printers"])


class PrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=127)
    device_uri: str = Field(min_length=6, max_length=512)
    description: str = ""
    location: str = ""
    ppd_model: str = "everywhere"
    deep_scan_required: bool = False
    fail_mode: str = "open"
    on_unreadable: str = "log"
    rule_tags: list[str] = Field(default_factory=lambda: ["*"])
    shared: bool = True
    note: str = ""


class PrinterPatch(BaseModel):
    device_uri: str | None = None
    description: str | None = None
    location: str | None = None
    deep_scan_required: bool | None = None
    fail_mode: str | None = None
    on_unreadable: str | None = None
    rule_tags: list[str] | None = None
    shared: bool | None = None
    enabled: bool | None = None
    note: str = ""


def _out(row: PrinterQueue) -> dict:
    return {
        "name": row.name,
        "device_uri": row.device_uri,
        "janus_uri": row.janus_uri if row.device_uri else "",
        "description": row.description,
        "location": row.location,
        "deep_scan_required": row.deep_scan_required,
        "fail_mode": row.fail_mode,
        "on_unreadable": row.on_unreadable,
        "rule_tags": row.rule_tags,
        "enabled": row.enabled,
        "shared": row.shared,
        "cups_state": row.cups_state,
        "cups_error": row.cups_error,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("")
def list_printers(
    session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> list[dict]:
    rows = session.scalars(select(PrinterQueue).order_by(PrinterQueue.name)).all()
    return [_out(row) for row in rows]


@router.get("/reconcile")
def reconcile(
    session: Session = Depends(get_session), _user: User = Depends(require_role("admin"))
) -> dict:
    """What is configured here versus what CUPS actually has.

    `missing_in_cups` is the dangerous column: those queues look configured in the console
    while inspecting nothing at all.
    """
    return printer_store.reconcile(session) | {"cups": cups_control.describe()}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_printer(
    payload: PrinterIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    body = payload.model_dump()
    note = body.pop("note", "")
    try:
        row = printer_store.create(session, body, actor=user.username, note=note)
    except (printer_store.PrinterError, cups_control.CupsControlError) as exc:
        raise _error(exc) from exc
    return _out(row)


@router.patch("/{name}")
def update_printer(
    name: str,
    payload: PrinterPatch,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    note = body.pop("note", "")
    try:
        row = printer_store.update(session, name, body, actor=user.username, note=note)
    except (printer_store.PrinterError, cups_control.CupsControlError) as exc:
        raise _error(exc) from exc
    return _out(row)


@router.delete("/{name}")
def delete_printer(
    name: str,
    note: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    try:
        printer_store.delete(session, name, actor=user.username, note=note)
    except printer_store.PrinterError as exc:
        raise _error(exc) from exc
    return {"deleted": name}


class TestPageIn(BaseModel):
    note: str = ""


@router.post("/{name}/test-connection")
def test_connection(
    name: str,
    session: Session = Depends(get_session),
    _user: User = Depends(require_role("admin")),
) -> dict:
    """Check the device answers, without printing anything.

    Two independent questions, reported separately: can we open a socket to the printer,
    and what does CUPS think of the queue. A queue can be disabled in CUPS while the
    device is perfectly healthy, and vice versa.
    """
    import socket
    import time

    row = session.get(PrinterQueue, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such queue")

    result: dict = {"queue": name, "device_uri": row.device_uri, "probed_from": "api"}

    if row.device_uri:
        try:
            host, port = cups_control.device_endpoint(row.device_uri)
            result["endpoint"] = f"{host}:{port}"
            if host in cups_control.LOOPBACK:
                # Loopback is relative to the spooler, not to us. Probing it here would
                # test the API container against itself and report a bogus failure.
                raise cups_control.CupsControlError(
                    "device address is loopback, which is only meaningful on the print "
                    "server itself — cannot probe it from here"
                )
            started = time.monotonic()
            with socket.create_connection((host, port), timeout=5):
                result["device_reachable"] = True
                result["latency_ms"] = int((time.monotonic() - started) * 1000)
        except cups_control.CupsControlError as exc:
            result["device_reachable"] = None
            result["device_error"] = str(exc)
        except OSError as exc:
            result["device_reachable"] = False
            result["device_error"] = str(exc)
            # The probe runs from the API container, which may not share the print
            # server's view of the network. Docker's default address pool is
            # 172.16.0.0/12, so a site using 172.16-172.31 addressing has its LAN
            # shadowed by container bridges: the printer is fine, the probe is lying.
            # CUPS' own state is the authoritative signal in that case.
            result["hint"] = (
                "Probed from the API container, not the print server. If your printers "
                "are on 172.16-172.31, Docker's default address pool shadows them — set "
                "default-address-pools in the Docker daemon config, or trust the CUPS "
                "state below instead."
            )
    else:
        result["device_reachable"] = None
        result["device_error"] = "no device URI recorded for this queue"

    try:
        result["cups"] = cups_control.printer_state(name)
    except cups_control.CupsControlError as exc:
        result["cups"] = {"state": "error", "detail": str(exc)}

    result["ok"] = bool(result.get("device_reachable")) and result["cups"].get(
        "state"
    ) not in {"error", "disabled", "unknown"}
    return result


@router.post("/{name}/test-page")
def print_test_page(
    name: str,
    payload: TestPageIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Send a real test page through the full path.

    Deliberately submitted to the inspected queue rather than straight to the device: the
    point is to prove interception, verdict and release all work, not just that the
    printer has paper. It is inspected like any other job and appears in the queue.
    """
    import os
    import tempfile

    from ..testpage import build_test_page

    row = session.get(PrinterQueue, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such queue")
    if not cups_control.available():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "CUPS control is disabled; set JANUS_PRINT_CUPS_CONTROL to submit test pages",
        )

    pdf = build_test_page(name, user.username, payload.note)
    handle, path = tempfile.mkstemp(prefix="janus-testpage-", suffix=".pdf")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(pdf)
        request_id = cups_control.submit_file(name, path, f"janus-print test page ({name})")
    except cups_control.CupsControlError as exc:
        detail = str(exc)
        if "not shared" in detail:
            detail = (
                "CUPS will not accept jobs for this queue from another host because it is "
                "not shared, and sharing cannot be set remotely. Apply sharing on the "
                "print server (restart the cups service, or use ssh control mode), then "
                "try again. Printing from workstations is unaffected."
            )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    printer_store._record(
        session, row, user.username, "test-page", payload.note or "test page submitted"
    )
    return {
        "queue": name,
        "submitted": True,
        "cups_request_id": request_id,
        "note": "Inspected like any other job — check the queue if it does not appear.",
    }


@router.get("/revisions")
def printer_revisions(
    queue: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict]:
    return [
        {
            "at": rev.at,
            "queue": rev.queue,
            "actor": rev.actor,
            "change": rev.change,
            "note": rev.note,
        }
        for rev in printer_store.revisions(session, queue, min(limit, 500))
    ]
