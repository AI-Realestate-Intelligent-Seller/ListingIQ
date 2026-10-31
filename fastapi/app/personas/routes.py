"""FastAPI endpoints for the Persona foundation."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Response, UploadFile, status
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import User
from ..routes.auth import get_current_user, get_db
from . import imports as persona_imports
from . import permissions, service, template_document
from .models import Persona, PersonaDefault
from .schemas import (
    MAX_DB_INT,
    PersonaCreate,
    PersonaDefaultOut,
    PersonaDefaultUpdate,
    PersonaDetail,
    PersonaDraftOut,
    PersonaDraftUpdate,
    PersonaEventOut,
    PersonaExampleBatch,
    PersonaExampleCreate,
    PersonaExampleMutation,
    PersonaExampleOut,
    PersonaExamplePage,
    PersonaExampleUpdate,
    PersonaImportApply,
    PersonaImportJobOut,
    PersonaOut,
    PersonaPublishRequest,
    PersonaSelfPublishRequest,
    PersonaRejectionRequest,
    PersonaSubmissionOut,
    PersonaSubmitRequest,
    PersonaValidationOut,
    PersonaVersionOut,
)

router = APIRouter()

# Identifiers are bounded to the database INTEGER range so oversized values
# are a 422, never a database overflow.
PersonaId = Path(..., ge=1, le=MAX_DB_INT)
VersionNumber = Path(..., ge=1, le=MAX_DB_INT)
SubmissionId = Path(..., ge=1, le=MAX_DB_INT)


def _call(session: Session, callback: Callable):
    """Run a service call and map domain and database errors to HTTP.

    Any database error rolls the whole request back, so a rejected request
    never leaves partial changes behind.
    """
    try:
        return callback()
    except permissions.PersonaAccessError as error:
        raise HTTPException(
            status_code=404 if error.not_found else 403,
            detail=str(error),
        ) from error
    except service.PersonaConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except service.PersonaValidationFailure as error:
        raise HTTPException(
            status_code=422,
            detail={"message": str(error), "errors": error.errors},
        ) from error
    except persona_imports.ImportFailure as error:
        code = {
            "file_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        }.get(error.code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise HTTPException(status_code=code, detail={"code": error.code, "message": str(error)}) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="The request conflicts with existing Persona data"
        ) from error
    except DataError as error:
        session.rollback()
        raise HTTPException(
            status_code=422,
            detail="The request contains a value the database cannot store",
        ) from error
    except ValueError as error:
        # Raised by the ORM immutability guards.
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError:
        session.rollback()
        raise


def _persona_out(persona: Persona) -> dict:
    active = persona.active_version
    return {
        "id": persona.id,
        "brokerage_id": persona.brokerage_id,
        "owner_user_id": persona.owner_user_id,
        "slug": persona.slug,
        "display_name": persona.display_name,
        "scope": persona.scope,
        "status": persona.status,
        "active_version_id": persona.active_version_id,
        "active_version_number": active.version_number if active else None,
        "membership_ended_at": persona.membership_ended_at,
        "created_by_user_id": persona.created_by_user_id,
        "created_at": persona.created_at,
        "updated_at": persona.updated_at,
    }


def _persona_detail(persona: Persona, current_user: User) -> dict:
    # The working draft is owner-only; reviewers use the submission endpoints.
    return {
        **_persona_out(persona),
        "draft": persona.draft if permissions.is_owner(current_user, persona) else None,
        "active_version": persona.active_version,
    }


def _default_out(target: User, channel: str, row: PersonaDefault | None) -> dict:
    persona = row.persona if row else None
    return {
        "channel": channel,
        "user_id": target.id,
        "persona": _persona_out(persona) if persona else None,
        "resolved_version": persona.active_version if persona else None,
    }


@router.get("", response_model=list[PersonaOut])
def list_personas(
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    rows = _call(
        session,
        lambda: service.list_accessible(session, current_user, include_archived),
    )
    return [_persona_out(row) for row in rows]


@router.post("", response_model=PersonaDetail, status_code=status.HTTP_201_CREATED)
def create_persona(
    payload: PersonaCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _persona_detail(
        _call(session, lambda: service.create(session, current_user, payload)),
        current_user,
    )


# The template's single definition lives in template_document; both formats
# use the labels the local template parser reads.
PERSONA_TEMPLATE = template_document.markdown_template()


@router.get("/template")
def download_persona_template(
    format: str = Query("docx", regex="^(docx|markdown)$"),
    current_user: User = Depends(get_current_user),
):
    del current_user
    if format == "markdown":
        return Response(
            PERSONA_TEMPLATE,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{template_document.MARKDOWN_FILENAME}"'},
        )
    return Response(
        template_document.authoritative_docx(),
        media_type=template_document.DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{template_document.DOCX_FILENAME}"'},
    )


# Defaults are declared before /{persona_id} so the static route remains
# unambiguous in every FastAPI/Starlette version used by the project.
@router.get("/defaults/{channel}", response_model=PersonaDefaultOut)
def get_persona_default(
    channel: str,
    user_id: int | None = Query(None, ge=1, le=MAX_DB_INT),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    target, row = _call(
        session, lambda: service.get_default(session, current_user, channel, user_id)
    )
    return _default_out(target, channel.lower(), row)


@router.put("/defaults/{channel}", response_model=PersonaDefaultOut)
def update_persona_default(
    channel: str,
    payload: PersonaDefaultUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    target, row = _call(
        session,
        lambda: service.set_default(
            session,
            current_user,
            channel,
            payload.persona_id,
            payload.user_id,
        ),
    )
    return _default_out(target, channel.lower(), row)


@router.get("/{persona_id}", response_model=PersonaDetail)
def get_persona(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _persona_detail(
        _call(session, lambda: service.get(session, current_user, persona_id)),
        current_user,
    )


@router.get("/{persona_id}/draft", response_model=PersonaDraftOut)
def get_persona_draft(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: service.get_draft(session, current_user, persona_id))


@router.post("/{persona_id}/imports", response_model=PersonaImportJobOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_persona_import(
    persona_id: int = PersonaId,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    content = await file.read(persona_imports.MAX_FILE_BYTES + 1)
    return _call(session, lambda: persona_imports.create_job(session, current_user, persona_id, file.filename or "persona-document", file.content_type or "application/octet-stream", content))


@router.get("/{persona_id}/imports", response_model=list[PersonaImportJobOut])
def list_persona_imports(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: persona_imports.list_jobs(session, current_user, persona_id))


@router.get("/{persona_id}/imports/{job_id}", response_model=PersonaImportJobOut)
def get_persona_import(
    job_id: str = Path(..., min_length=32, max_length=32),
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: persona_imports.get_job(session, current_user, persona_id, job_id))


@router.post("/{persona_id}/imports/{job_id}/retry", response_model=PersonaImportJobOut, status_code=status.HTTP_202_ACCEPTED)
def retry_persona_import(
    job_id: str = Path(..., min_length=32, max_length=32),
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: persona_imports.retry_job(session, current_user, persona_id, job_id))


@router.post("/{persona_id}/imports/{job_id}/ai-interpretation", response_model=PersonaImportJobOut, status_code=status.HTTP_202_ACCEPTED)
def request_persona_import_ai_interpretation(
    job_id: str = Path(..., min_length=32, max_length=32),
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Owner-only, explicit opt-in: send this document to the configured AI provider."""
    return _call(session, lambda: persona_imports.request_ai_interpretation(session, current_user, persona_id, job_id))


@router.post("/{persona_id}/imports/{job_id}/apply", response_model=PersonaDraftOut)
def apply_persona_import(
    payload: PersonaImportApply,
    job_id: str = Path(..., min_length=32, max_length=32),
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: persona_imports.apply_job(session, current_user, persona_id, job_id, payload.revision, payload.reviewed))


@router.patch("/{persona_id}/draft", response_model=PersonaDraftOut)
def update_persona_draft(
    payload: PersonaDraftUpdate,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.update_draft(session, current_user, persona_id, payload),
    )


def _example_out(row, revision: int | None = None) -> dict:
    return {
        "id": row.id,
        "position": row.position,
        "scenario": row.scenario,
        "channel": row.channel,
        "language": row.language,
        "customer_message": row.customer_message,
        "preferred_response": row.preferred_response,
        "context": row.context,
        "style_explanation": row.style_explanation,
        "provenance": row.provenance,
        "owner_approved": bool(row.owner_approved),
        "review_flags": list(row.review_flags or []),
        "revision": revision,
    }


@router.get("/{persona_id}/examples", response_model=PersonaExamplePage)
def list_persona_draft_examples(
    persona_id: int = PersonaId,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    channel: str | None = Query(None, regex="^(sms|email)$"),
    scenario: str | None = Query(None, max_length=120),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    items, total, revision = _call(session, lambda: service.list_draft_examples(session, current_user, persona_id, page, page_size, channel, scenario))
    return {"items": [_example_out(row) for row in items], "total": total, "page": page, "page_size": page_size, "revision": revision}


@router.put("/{persona_id}/examples", response_model=PersonaExamplePage)
def save_persona_examples(
    payload: PersonaExampleBatch,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    rows = _call(session, lambda: service.save_examples(session, current_user, persona_id, payload))
    persona = session.get(Persona, persona_id)
    return {"items": [_example_out(row) for row in rows], "total": len(rows), "page": 1,
            "page_size": max(len(rows), 1), "revision": persona.draft.revision}


@router.post("/{persona_id}/examples", response_model=PersonaExampleOut, status_code=status.HTTP_201_CREATED)
def create_persona_example(
    payload: PersonaExampleCreate,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    row = _call(session, lambda: service.create_example(session, current_user, persona_id, payload))
    return _example_out(row, row.persona.draft.revision)


@router.patch("/{persona_id}/examples/{example_id}", response_model=PersonaExampleOut)
def update_persona_example(
    payload: PersonaExampleUpdate,
    persona_id: int = PersonaId,
    example_id: int = Path(..., ge=1, le=MAX_DB_INT),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    row = _call(session, lambda: service.update_example(session, current_user, persona_id, example_id, payload))
    return _example_out(row, row.persona.draft.revision)


@router.delete("/{persona_id}/examples/{example_id}")
def delete_persona_example(
    payload: PersonaExampleMutation,
    persona_id: int = PersonaId,
    example_id: int = Path(..., ge=1, le=MAX_DB_INT),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    revision = _call(session, lambda: service.delete_example(session, current_user, persona_id, example_id, payload.revision))
    return {"deleted": True, "revision": revision}


@router.post("/{persona_id}/examples/{example_id}/duplicate", response_model=PersonaExampleOut, status_code=status.HTTP_201_CREATED)
def duplicate_persona_example(
    payload: PersonaExampleMutation,
    persona_id: int = PersonaId,
    example_id: int = Path(..., ge=1, le=MAX_DB_INT),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    row = _call(session, lambda: service.duplicate_example(session, current_user, persona_id, example_id, payload.revision))
    return _example_out(row, row.persona.draft.revision)


@router.post("/{persona_id}/validate", response_model=PersonaValidationOut)
def validate_persona_draft(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: service.validate(session, current_user, persona_id))


@router.post("/{persona_id}/submit", response_model=PersonaSubmissionOut)
def submit_persona_draft(
    payload: PersonaSubmitRequest,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.submit(session, current_user, persona_id, payload.revision),
    )


@router.post(
    "/{persona_id}/publish",
    response_model=PersonaVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def publish_persona(
    payload: PersonaPublishRequest,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.publish(
            session, current_user, persona_id, payload.submission_id
        ),
    )


@router.post("/{persona_id}/versions", response_model=PersonaVersionOut, status_code=status.HTTP_201_CREATED)
def publish_own_persona(
    payload: PersonaSelfPublishRequest,
    response: Response,
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Owner self-publication of a Personal Persona; no HOB review is involved."""
    version, created = _call(session, lambda: service.publish_own(session, current_user, persona_id, payload.revision))
    if not created:
        response.status_code = status.HTTP_200_OK
    return version


@router.get("/{persona_id}/submissions", response_model=list[PersonaSubmissionOut])
def list_persona_submissions(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session, lambda: service.submissions(session, current_user, persona_id)
    )


@router.get(
    "/{persona_id}/submissions/{submission_id}",
    response_model=PersonaSubmissionOut,
)
def get_persona_submission(
    persona_id: int = PersonaId,
    submission_id: int = SubmissionId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.submission(session, current_user, persona_id, submission_id),
    )


@router.get("/{persona_id}/submissions/{submission_id}/examples", response_model=PersonaExamplePage)
def get_persona_submission_examples(
    persona_id: int = PersonaId,
    submission_id: int = SubmissionId,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    items, total = _call(session, lambda: service.list_submission_examples(session, current_user, persona_id, submission_id, page, page_size))
    return {"items": [_example_out(row) for row in items], "total": total, "page": page, "page_size": page_size}


@router.post(
    "/{persona_id}/submissions/{submission_id}/reject",
    response_model=PersonaSubmissionOut,
)
def reject_persona_submission(
    payload: PersonaRejectionRequest,
    persona_id: int = PersonaId,
    submission_id: int = SubmissionId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.reject_submission(
            session,
            current_user,
            persona_id,
            submission_id,
            payload.reason,
        ),
    )


@router.get("/{persona_id}/versions", response_model=list[PersonaVersionOut])
def list_persona_versions(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: service.versions(session, current_user, persona_id))


@router.get("/{persona_id}/versions/{version_number}", response_model=PersonaVersionOut)
def get_persona_version(
    persona_id: int = PersonaId,
    version_number: int = VersionNumber,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.version(session, current_user, persona_id, version_number),
    )


@router.get("/{persona_id}/versions/{version_number}/examples", response_model=PersonaExamplePage)
def get_persona_version_examples(
    persona_id: int = PersonaId,
    version_number: int = VersionNumber,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    items, total = _call(session, lambda: service.list_version_examples(session, current_user, persona_id, version_number, page, page_size))
    return {"items": [_example_out(row) for row in items], "total": total, "page": page, "page_size": page_size}


@router.post(
    "/{persona_id}/versions/{version_number}/activate",
    response_model=PersonaVersionOut,
)
def activate_persona_version(
    persona_id: int = PersonaId,
    version_number: int = VersionNumber,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(
        session,
        lambda: service.activate(session, current_user, persona_id, version_number),
    )


@router.post("/{persona_id}/archive", response_model=PersonaOut)
def archive_persona(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _persona_out(
        _call(session, lambda: service.archive(session, current_user, persona_id))
    )


@router.get("/{persona_id}/events", response_model=list[PersonaEventOut])
def list_persona_events(
    persona_id: int = PersonaId,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return _call(session, lambda: service.events(session, current_user, persona_id))
