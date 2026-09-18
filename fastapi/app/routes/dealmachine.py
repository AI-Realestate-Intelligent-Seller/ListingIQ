# ruff: noqa: B008

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..dealmachine.client import ProviderError
from ..dealmachine.config import (
    ActivitySearchRequest,
    CategoryConfig,
    ContactEnrichmentRequest,
    DetailsRequest,
    ExportRequest,
    ListItemsRequest,
    ListRequest,
    SearchRequest,
    Settings,
)
from ..dealmachine.models import SavedFile
from ..dealmachine.service import Service, UnsafeOperation, operation_lock
from ..models import User
from .auth import get_db
from .platform_admin import audit, require_platform_admin

router = APIRouter(dependencies=[Depends(require_platform_admin)])


def service(db: Session = Depends(get_db)):
    return Service(db)


def finish(s, actor, action, result, detail=None):
    audit(
        s.db, actor, f"dealmachine.{action}", "integration", "dealmachine", detail or {}
    )
    s.db.commit()
    return result


def fail(error):
    if isinstance(error, UnsafeOperation):
        raise HTTPException(409, str(error)) from None
    if isinstance(error, ProviderError):
        status = (
            401
            if error.status_code in (401, 403)
            else 429
            if error.status_code == 429
            else 502
        )
        raise HTTPException(status, str(error)) from None
    raise error


@router.get("")
def overview(s: Service = Depends(service)):
    return s.summary()


@router.post("/connection/test")
def test_connection(
    s: Service = Depends(service), actor: User = Depends(require_platform_admin)
):
    try:
        return finish(s, actor, "connection.test", s.test_connection(actor.id))
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.get("/filters")
def filters(
    refresh: bool = False,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "filters.read",
            s.metadata("filter", refresh, actor.id),
            {"refresh": refresh},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.get("/fields")
def fields(
    refresh: bool = False,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "fields.read",
            s.metadata("field", refresh, actor.id),
            {"refresh": refresh},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.get("/usage")
def usage(s: Service = Depends(service), actor: User = Depends(require_platform_admin)):
    try:
        return finish(s, actor, "usage.read", s.usage(actor.id))
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/properties/count")
def property_count(
    payload: SearchRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "properties.count",
            s.count(payload, actor.id),
            {"categories": payload.category_ids},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/properties/estimate")
def property_estimate(
    payload: SearchRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "properties.estimate",
            s.estimate(payload, actor.id),
            {"categories": payload.category_ids},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/properties/search")
def property_search(
    payload: SearchRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "properties.search",
            s.search_selected(payload, actor.id),
            {"reason": payload.reason, "categories": payload.category_ids},
        )
    except (UnsafeOperation, ProviderError) as error:
        audit(
            s.db,
            actor,
            "dealmachine.properties.search_failed",
            "integration",
            "dealmachine",
            {"reason": payload.reason, "error": str(error)},
        )
        s.db.commit()
        fail(error)


@router.post("/properties/details")
def property_details(
    payload: DetailsRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "properties.details",
            s.details(payload, actor.id),
            {"reason": payload.reason},
        )
    except (UnsafeOperation, ProviderError) as error:
        audit(
            s.db,
            actor,
            "dealmachine.properties.details_failed",
            "integration",
            "dealmachine",
            {"reason": payload.reason, "error": str(error)},
        )
        s.db.commit()
        fail(error)


@router.post("/contacts/enrich")
def enrich_contacts(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "contacts.enrich",
            s.enrich_contacts(payload, actor.id),
            {"reason": payload.reason, "forced": payload.force},
        )
    except (UnsafeOperation, ProviderError) as error:
        audit(
            s.db,
            actor,
            "dealmachine.contacts.enrich_failed",
            "integration",
            "dealmachine",
            {"reason": payload.reason, "error": str(error)},
        )
        s.db.commit()
        fail(error)


@router.post("/lists")
def create_list(
    payload: ListRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "lists.create",
            s.create_list(payload, actor.id),
            {"categories": payload.category_ids},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.get("/lists/{list_id}")
def get_list_status(
    list_id: str,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(s, actor, "lists.status", s.list_status(list_id, actor.id))
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/lists/{list_id}/items")
def add_list_items(
    list_id: str,
    payload: ListItemsRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "lists.items.add",
            s.list_items(list_id, payload, actor.id),
            {"count": len(payload.ids)},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.delete("/lists/{list_id}/items")
def remove_list_items(
    list_id: str,
    payload: ListItemsRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "lists.items.remove",
            s.list_items(list_id, payload, actor.id, True),
            {"count": len(payload.ids), "reason": payload.reason},
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/activity/search")
def search_activity(
    payload: ActivitySearchRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(s, actor, "activity.search", s.activity_search(payload, actor.id))
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.get("/activity/{activity_id}")
def get_activity(
    activity_id: str,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s, actor, "activity.detail", s.activity_detail(activity_id, actor.id)
        )
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.post("/properties/export")
def export_properties(
    payload: ExportRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "properties.export",
            s.export_properties(payload, actor.id),
            {
                "reason": payload.reason,
                "expected_count": payload.expected_count,
                "estimated_credits": payload.estimated_credits,
            },
        )
    except (UnsafeOperation, ProviderError) as error:
        audit(
            s.db,
            actor,
            "dealmachine.properties.export_failed",
            "integration",
            "dealmachine",
            {"reason": payload.reason, "error": str(error)},
        )
        s.db.commit()
        fail(error)


@router.get("/history")
def history(
    endpoint: str | None = Query(None, max_length=240),
    status: str | None = Query(None, max_length=30),
    category: str | None = Query(None, max_length=60),
    user_id: int | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=250),
    s: Service = Depends(service),
):
    return s.history(offset, limit, endpoint, status, category, user_id)


@router.get("/history/{run_id}")
def history_detail(run_id: str, s: Service = Depends(service)):
    try:
        return s.history_detail(run_id)
    except UnsafeOperation as error:
        fail(error)


@router.get("/files/{file_id}/download")
def download_file(file_id: int, s: Service = Depends(service)):
    row = s.db.query(SavedFile).filter_by(id=file_id).one_or_none()
    if not row:
        raise HTTPException(404, "DealMachine file not found")
    root = s._root()
    path = (root / row.relative_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Stored file is unavailable")
    return FileResponse(path, media_type=row.mime_type, filename=path.name)


@router.get("/settings")
def get_settings(s: Service = Depends(service)):
    return s.summary()


@router.put("/settings")
def put_settings(
    payload: Settings,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        with operation_lock("settings"):
            return finish(
                s, actor, "settings.update", s.save_settings(payload, actor.id)
            )
    except UnsafeOperation as error:
        s.db.commit()
        fail(error)


@router.post("/settings/test")
def test_settings(
    s: Service = Depends(service), actor: User = Depends(require_platform_admin)
):
    try:
        return finish(s, actor, "settings.test", s.test_connection(actor.id))
    except (UnsafeOperation, ProviderError) as error:
        s.db.commit()
        fail(error)


@router.put("/categories/{category_id}")
def update_category(
    category_id: str,
    payload: CategoryConfig,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        return finish(
            s,
            actor,
            "category.update",
            s.save_category(category_id, payload),
            {"category": category_id},
        )
    except UnsafeOperation as error:
        s.db.commit()
        fail(error)
