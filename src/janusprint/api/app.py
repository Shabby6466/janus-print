"""FastAPI application: print path, console, and admin surface in one process."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ..bridge.cef import get_bridge
from ..config import get_settings
from ..db import get_engine, init_db, session_scope
from ..inspector.engine import get_ruleset
from ..inspector.rules import test_fixtures
from ..schemas import HealthOut
from . import (
    routes_admin,
    routes_console,
    routes_inspect,
    routes_install,
    routes_jobs,
    routes_printers,
    routes_users,
)
from .auth import ensure_admin_user

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    init_db()

    # The YAML packs seed the rules table once. Existing rules are never overwritten, so a
    # restart can't silently revert an operator's edits.
    from ..inspector.store import seed_from_yaml

    from .. import printers as printer_store

    with session_scope() as session:
        seeded = seed_from_yaml(session)
        printer_store.seed_from_yaml(session)
        # A queue created by hand with lpadmin would otherwise be invisible here while
        # silently running the default policy.
        printer_store.adopt_existing(session)
    if seeded:
        log.info("seeded %d rules from the shipped YAML packs", seeded)

    ruleset = get_ruleset()
    failures = test_fixtures(ruleset)
    if failures:
        # Loud, but not fatal — refusing to start would stop printing, which is worse
        # than running with a rule whose fixtures regressed.
        log.error(
            "%d rule fixture failures at startup: %s",
            len(failures),
            ", ".join(sorted({f.rule_id for f in failures})),
        )

    if settings.dev_mode:
        import os

        with session_scope() as session:
            ensure_admin_user(
                session,
                os.environ.get("JANUS_PRINT_ADMIN_USER", "admin"),
                os.environ.get("JANUS_PRINT_ADMIN_PASSWORD", "janus-print"),
            )
        log.warning("dev mode: default admin user ensured; never do this in production")

    get_bridge().send_operational(
        "SERVICE_START",
        "janus-print inspector started",
        3,
        cs1=str(len(ruleset)),
        cs1Label="rulesLoaded",
    )
    log.info("janus-print ready: %d rules, archive=%s", len(ruleset), settings.archive_backend)
    yield
    get_bridge().send_operational("SERVICE_STOP", "janus-print inspector stopped", 5)


def create_app() -> FastAPI:
    app = FastAPI(
        title="janus-print",
        description="Print DLP — inspect at the spooler, hold on match, alert Janus SIEM",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(routes_inspect.router, prefix="/api/v1")
    app.include_router(routes_jobs.router, prefix="/api/v1")
    app.include_router(routes_admin.router, prefix="/api/v1")
    app.include_router(routes_users.router, prefix="/api/v1")
    app.include_router(routes_printers.router, prefix="/api/v1")
    app.include_router(routes_install.router)
    app.include_router(routes_console.router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/v1/health", response_model=HealthOut, tags=["ops"])
    def health() -> HealthOut:
        settings = get_settings()
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ok"
        except Exception as exc:  # noqa: BLE001
            database = f"error: {exc}"

        return HealthOut(
            status="ok" if database == "ok" else "degraded",
            rules_loaded=len(get_ruleset()),
            ocr_available=shutil.which("tesseract") is not None,
            ghostscript_available=shutil.which("gs") is not None,
            archive_backend=settings.archive_backend,
            siem_enabled=settings.siem_enabled,
            database=database,
        )

    return app


app = create_app()


def main() -> int:
    import uvicorn

    uvicorn.run(
        "janusprint.api.app:app",
        host="0.0.0.0",  # noqa: S104 - container-internal, fronted by the compose network
        port=8080,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
