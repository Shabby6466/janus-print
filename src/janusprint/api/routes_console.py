"""Server-rendered SOC console. No SPA, no build step, no CDN — this runs on print
servers that often have no egress at all.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_printer_policies, get_settings
from ..db import get_session
from ..inspector import store as rule_store
from ..inspector.engine import get_ruleset
from ..inspector.validators import VALIDATORS
from ..models import (
    ArchiveAccess,
    ContentRequest,
    Job,
    JobState,
    RegisteredDocument,
    RuleRow,
    User,
)
from . import cups_control
from .auth import (
    SESSION_COOKIE,
    authenticate,
    create_session,
    current_user_optional,
    destroy_session,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(include_in_schema=False)

STATE_LABELS = {
    JobState.held: ("Held", "warn"),
    JobState.blocked: ("Blocked", "bad"),
    JobState.released: ("Released", "ok"),
    JobState.released_by_analyst: ("Released by analyst", "ok"),
    JobState.denied_by_analyst: ("Denied", "bad"),
    JobState.failed_open: ("Failed open", "gap"),
    JobState.inspecting: ("Inspecting", "muted"),
    JobState.error: ("Error", "bad"),
}
TEMPLATES.env.globals["STATE_LABELS"] = STATE_LABELS


def _rule_dict(row: RuleRow) -> dict:
    """Templates take plain dicts so the same shape serves both the list and the editor."""
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "pattern": row.pattern,
        "action": row.action,
        "severity": row.severity,
        "validator": row.validator,
        "base_confidence": row.base_confidence,
        "threshold": row.threshold,
        "min_count": row.min_count,
        "ignore_case": row.ignore_case,
        "tags": row.tags or [],
        "context": row.context or {},
        "fixtures": row.fixtures or {},
        "enabled": row.enabled,
        "source": row.source,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


def _render(request: Request, template: str, user: User | None, **context) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, template, {"user": user, "settings": get_settings(), **context}
    )


def _login_redirect(next_path: str = "/") -> RedirectResponse:
    return RedirectResponse(f"/login?next={next_path}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_form(request: Request, next: str = "/") -> HTMLResponse:
    return _render(request, "login.html", None, next=next, error=None)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    session: Session = Depends(get_session),
) -> Response:
    user = authenticate(session, username, password)
    if user is None:
        return _render(request, "login.html", None, next=next, error="Invalid credentials")

    token = create_session(session, user)
    response = RedirectResponse(next or "/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=get_settings().session_ttl_seconds,
    )
    return response


@router.get("/logout")
def logout(request: Request, session: Session = Depends(get_session)) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        destroy_session(session, token)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/")
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/")

    counts = dict(
        session.execute(select(Job.state, func.count()).group_by(Job.state)).all()
    )
    held = list(
        session.scalars(
            select(Job).where(Job.state == JobState.held).order_by(Job.created_at.desc()).limit(25)
        )
    )
    recent = list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(25)))
    gaps = counts.get(JobState.failed_open, 0)
    pending_requests = session.scalar(
        select(func.count()).select_from(ContentRequest).where(ContentRequest.status == "pending")
    )

    return _render(
        request,
        "dashboard.html",
        user,
        counts=counts,
        held=held,
        recent=recent,
        gaps=gaps,
        pending_requests=pending_requests or 0,
        rules_loaded=len(get_ruleset()),
        cups=cups_control.describe(),
    )


@router.get("/queue")
def queue_view(
    request: Request,
    state: str = "held",
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/queue")

    query = select(Job).order_by(Job.created_at.desc()).limit(200)
    if state != "all":
        query = query.where(Job.state == JobState(state))
    jobs = list(session.scalars(query))
    return _render(
        request, "queue.html", user, jobs=jobs, state=state, states=[s.value for s in JobState]
    )


@router.get("/jobs/{job_id}")
def job_view(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect(f"/jobs/{job_id}")

    job = session.get(Job, job_id)
    if job is None:
        return _render(request, "notfound.html", user, what=f"job {job_id}")

    requests_ = list(
        session.scalars(
            select(ContentRequest)
            .where(ContentRequest.job_id == job_id)
            .order_by(ContentRequest.created_at.desc())
        )
    )
    policy = get_printer_policies().for_queue(job.queue)
    return _render(
        request,
        "job.html",
        user,
        job=job,
        content_requests=requests_,
        policy=policy,
        events=sorted(job.events, key=lambda e: e.at),
    )


@router.get("/rules")
def rules_view(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/rules")
    rows = session.scalars(
        select(RuleRow).order_by(RuleRow.enabled.desc(), RuleRow.severity.desc(), RuleRow.id)
    ).all()
    return _render(request, "rules.html", user, rules=[_rule_dict(row) for row in rows])


@router.get("/rules/new")
def rule_new(request: Request, user: User | None = Depends(current_user_optional)):
    if user is None:
        return _login_redirect("/rules/new")
    if user.role != "admin":
        return _render(request, "notfound.html", user, what="that page (admin only)")
    return _render(
        request, "rule_edit.html", user, rule={}, creating=True, validators=sorted(VALIDATORS)
    )


@router.get("/rules/history")
def rule_history(
    request: Request,
    rule_id: str | None = None,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/rules/history")
    return _render(
        request,
        "rule_history.html",
        user,
        rule_id=rule_id,
        revisions=rule_store.revisions(session, rule_id, 300),
    )


@router.get("/rules/{rule_id}")
def rule_edit(
    rule_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect(f"/rules/{rule_id}")
    if user.role != "admin":
        return _render(request, "notfound.html", user, what="that page (admin only)")
    row = session.get(RuleRow, rule_id)
    if row is None:
        return _render(request, "notfound.html", user, what=f"rule {rule_id}")
    return _render(
        request,
        "rule_edit.html",
        user,
        rule=_rule_dict(row),
        creating=False,
        validators=sorted(VALIDATORS),
    )


@router.get("/documents")
def documents_view(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/documents")
    documents = list(
        session.scalars(
            select(RegisteredDocument).order_by(RegisteredDocument.created_at.desc())
        )
    )
    return _render(request, "documents.html", user, documents=documents)


@router.get("/audit")
def audit_view(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return _login_redirect("/audit")
    rows = list(
        session.scalars(select(ArchiveAccess).order_by(ArchiveAccess.at.desc()).limit(300))
    )
    pending = list(
        session.scalars(
            select(ContentRequest)
            .where(ContentRequest.status == "pending")
            .order_by(ContentRequest.created_at.desc())
        )
    )
    return _render(request, "audit.html", user, rows=rows, pending=pending)


@router.get("/policies")
def policies_view(
    request: Request, user: User | None = Depends(current_user_optional)
):
    if user is None:
        return _login_redirect("/policies")
    policies = get_printer_policies()
    return _render(
        request,
        "policies.html",
        user,
        default=policies.default,
        queues=policies.queues,
        cups=cups_control.describe(),
    )
