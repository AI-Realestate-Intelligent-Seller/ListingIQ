
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .logger import set_request_context, setup_logging
from .routes import (
    ai,
    assignments,
    auth,
    batchdata,
    calendar,
    campaigns,
    conversations,
    dealmachine,
    followups,
    integration_data,
    leads,
    messages,
    platform_admin,
    propertyradar,
    sms,
    team,
    webhooks,
    location,
)

from .routes.websocket import router as websocket_router
from .routes.push import router as push_router
from .reminder.worker import start_reminder_worker

app = FastAPI(title="ListingIQ API")

setup_logging()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    # generate session id per request
    set_request_context(session_id=None, user_id=None)
    response = await call_next(request)
    # expose session id to clients
    try:
        from .logger import session_id_var

        response.headers["X-Session-Id"] = session_id_var.get()
    except Exception:
        pass
    return response


from .db import init_db

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(team.router, prefix="/api/v1/team", tags=["team"])
app.include_router(sms.router, prefix="/api/v1/sms", tags=["sms"])
app.include_router(leads.router, prefix="/api/v1/leads", tags=["leads"])
app.include_router(followups.router, prefix="/api/v1/followups", tags=["followups"])
app.include_router(campaigns.router, prefix="/api/v1/campaigns", tags=["campaigns"])
app.include_router(
    assignments.router, prefix="/api/v1/assignments", tags=["assignments"]
)
app.include_router(calendar.router, prefix="/api/v1/auth/google", tags=["calendar"])
app.include_router(messages.router, prefix="/api/v1/messages", tags=["messages"])
app.include_router(
    conversations.router, prefix="/api/v1/conversations", tags=["conversations"]
)
app.include_router(location.router, prefix="/api/v1/location", tags=["location"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["webhooks"])
app.include_router(ai.router, prefix="/api/v1/ai", tags=["ai"])
app.include_router(platform_admin.router, prefix="/api/v1/platform-admin", tags=["platform-admin"])
app.include_router(push_router)
app.include_router(websocket_router)

app.include_router(
    propertyradar.index_router,
    prefix="/api/v1/platform-admin/integrations",
    tags=["integrations"],
)

app.include_router(
    propertyradar.router,
    prefix="/api/v1/integrations/propertyradar",
    tags=["propertyradar"],
)

app.include_router(
    propertyradar.webhook_router,
    prefix="/api/v1/webhooks",
    tags=["propertyradar-webhooks"],
)

app.include_router(
    batchdata.router,
    prefix="/api/v1/integrations/batchdata",
    tags=["batchdata"],
)

app.include_router(
    batchdata.webhook_router,
    prefix="/api/v1/webhooks",
    tags=["batchdata-webhooks"],
)

app.include_router(
    dealmachine.router,
    prefix="/api/v1/integrations/dealmachine",
    tags=["dealmachine"],
)

app.include_router(
    integration_data.router,
    prefix="/api/v1/platform-admin/integrations/data",
    tags=["integration-data"],
)
@app.on_event("startup")
def startup_event():
    init_db()
    from .db import SessionLocal
    from .propertyradar.service import initialize

    with SessionLocal() as db:
        initialize(db)
        from .batchdata.service import initialize as initialize_batchdata

        initialize_batchdata(db)
        from .dealmachine.service import initialize as initialize_dealmachine

        initialize_dealmachine(db)
    from .sms.followup_scheduler import start_followup_scheduler

    start_followup_scheduler()
    _warm_knowledge_base()
    start_reminder_worker()


def _warm_knowledge_base():
    """Build Bobbie's retrieval index in the background.

    Loading the embedding model and embedding the PDF takes a few seconds; doing
    it here keeps the first owner reply fast without delaying startup.
    """
    import threading

    def warm():
        try:
            from .sms.knowledge_index import bobbie_knowledge

            bobbie_knowledge.ensure_indexed()
        except Exception as error:  # never block or crash startup
            from .logger import get_logger

            get_logger(__name__).warning("[bobbie-rag] warm-up skipped: %s", error)

    threading.Thread(target=warm, name="knowledge-warmup", daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/stores")
def health_stores():
    """Connectivity of every backing store, for local setup checks."""
    from sqlalchemy import text as sql_text

    from .core.config import settings
    from .db import engine

    report = {}

    try:
        with engine.connect() as connection:
            connection.execute(sql_text("SELECT 1"))
        report["database"] = {
            "ok": True,
            "url": settings["database"]["url"].split("@")[-1],
        }
    except Exception as error:
        report["database"] = {"ok": False, "error": str(error)[:200]}

    try:
        import redis

        client = redis.Redis.from_url(settings["redis_url"], socket_connect_timeout=2)
        report["redis"] = {"ok": bool(client.ping()), "url": settings["redis_url"]}
    except Exception as error:
        report["redis"] = {
            "ok": False,
            "url": settings["redis_url"],
            "error": str(error)[:200],
        }

    try:
        from .vector_store import vector_store

        report["vector_store"] = {
            "ok": vector_store.available,
            "engine": "chromadb",
            "path": vector_store.path,
            "collections": vector_store.collections(),
        }
    except Exception as error:
        report["vector_store"] = {"ok": False, "error": str(error)[:200]}

    from pathlib import Path

    pdf = Path(settings["sms"]["knowledge_pdf"])
    report["knowledge_pdf"] = {"ok": pdf.is_file(), "path": str(pdf)}

    report["status"] = (
        "ok"
        if all(item.get("ok") for item in report.values() if isinstance(item, dict))
        else "degraded"
    )
    return report
