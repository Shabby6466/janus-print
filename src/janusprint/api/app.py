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
    routes_auth,
    routes_console,
    routes_inspect,
    routes_install,
    routes_jobs,
    routes_printers,
    routes_users,
    routes_validators,
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

    from .. import validator_store

    with session_scope() as session:
        seeded = seed_from_yaml(session)
        printer_store.seed_from_yaml(session)
        # A queue created by hand with lpadmin would otherwise be invisible here while
        # silently running the default policy.
        printer_store.adopt_existing(session)
        validator_store.seed_builtins(session)
        # resolve() runs on the print path and must stay a synchronous dict lookup, so any
        # console-defined validator has to be compiled into memory before the first job —
        # otherwise the first request after a restart would reject it as unknown.
        validator_store.refresh_registry(session, force=True)
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
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse

    app = FastAPI(
        title="janus-print",
        description="Print DLP — inspect at the spooler, hold on match, alert Janus SIEM",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8088", "http://127.0.0.1:8088"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_auth.router, prefix="/api/v1")
    app.include_router(routes_inspect.router, prefix="/api/v1")
    app.include_router(routes_jobs.router, prefix="/api/v1")
    app.include_router(routes_admin.router, prefix="/api/v1")
    app.include_router(routes_users.router, prefix="/api/v1")
    app.include_router(routes_printers.router, prefix="/api/v1")
    app.include_router(routes_validators.router, prefix="/api/v1")
    app.include_router(routes_install.router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Locate React SPA build directory if available
    dist_paths = [
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
        Path("/app/frontend/dist"),
        Path(__file__).resolve().parent.parent / "static" / "dist",
    ]
    dist_dir = None
    for p in dist_paths:
        if p.exists() and (p / "index.html").exists():
            dist_dir = p
            break

    if dist_dir:
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="spa_assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("static/") or full_path.startswith("assets/"):
                raise HTTPException(404)
            target = dist_dir / full_path
            if target.is_file():
                return FileResponse(str(target))
            return FileResponse(str(dist_dir / "index.html"))
    else:
        app.include_router(routes_console.router)

    @app.get("/api/v1/health", response_model=HealthOut, tags=["ops"])
    def health() -> HealthOut:
        settings = get_settings()
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ok"
        except Exception as exc:  # noqa: BLE001
            database = f"error: {exc}"

        from ..inspector import regex_engine

        return HealthOut(
            status="ok" if database == "ok" else "degraded",
            regex_engine=regex_engine.ENGINE,
            regex_linear_time=regex_engine.LINEAR_TIME,
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
