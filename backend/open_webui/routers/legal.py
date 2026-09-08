import asyncio
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File, Form, status
from fastapi.responses import JSONResponse, StreamingResponse
from open_webui.env import LEGAL_DEPARTMENT_ENABLED, VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT = httpx.Timeout(60.0)


def _service_headers(user, content_type: str = "application/json") -> dict:
    return {
        "Authorization": f"Bearer {VESQOR_SERVICE_TOKEN}",
        "X-VESQOR-User-Email": user.email,
        "Content-Type": content_type,
    }


def _require_enabled():
    """Feature flag gate. Matches the brain contract: when the department is
    disabled every legal route is indistinguishable from an unknown route (404),
    so clients cannot probe whether the feature exists."""
    if not LEGAL_DEPARTMENT_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )


async def _proxy_json(method: str, path: str, user, json_body: Optional[dict] = None) -> Response:
    _require_enabled()

    if not VESQOR_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VESQOR integration not configured",
        )

    url = f"{VESQOR_API_BASE_URL.rstrip('/')}{path}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                headers=_service_headers(user),
                json=json_body,
            )
    except httpx.RequestError as e:
        log.exception(f"VESQOR legal request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service is currently unreachable",
        )

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for VESQOR",
        )

    if response.status_code >= 500:
        log.error(f"VESQOR legal returned {response.status_code}: {response.text[:500]}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service error",
        )

    # Pass through JSON bodies (incl. 4xx with `blockers`) untouched so the
    # frontend sees the exact contract object documented in API_CONTRACT.md.
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    content_type = response.headers.get("content-type", "application/json")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=content_type,
    )


async def _proxy_multipart(path: str, user, file: UploadFile, template_id: Optional[str] = None) -> Response:
    _require_enabled()

    if not VESQOR_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VESQOR integration not configured",
        )

    url = f"{VESQOR_API_BASE_URL.rstrip('/')}{path}"

    data = {}
    if template_id:
        data["templateId"] = template_id

    files = {"file": (file.filename or "upload", await file.read(), file.content_type or "application/octet-stream")}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url,
                headers=_service_headers(user, "multipart/form-data"),
                data=data,
                files=files,
            )
    except httpx.RequestError as e:
        log.exception(f"VESQOR legal request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service is currently unreachable",
        )
    finally:
        await file.close()

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for VESQOR",
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
    )


async def _proxy_binary(method: str, path: str, user, json_body: Optional[dict] = None) -> Response:
    """Proxy for render endpoints that return docx/pdf binary files with a
    Content-Disposition attachment header."""
    _require_enabled()

    if not VESQOR_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VESQOR integration not configured",
        )

    url = f"{VESQOR_API_BASE_URL.rstrip('/')}{path}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                headers=_service_headers(user),
                json=json_body,
            )
    except httpx.RequestError as e:
        log.exception(f"VESQOR legal request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service is currently unreachable",
        )

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for VESQOR",
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    disposition = response.headers.get("content-disposition", "")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": disposition},
    )


############################
# Templates & documents
############################


@router.get("/templates")
async def list_templates(user=Depends(get_verified_user)):
    return await _proxy_json("GET", "/api/v1/legal/templates", user)


class CreateDocumentForm(BaseModel):
    templateId: str
    counterparty: Optional[dict] = None
    effectiveDate: Optional[str] = None
    prefill: Optional[dict] = None
    signatoryId: Optional[str] = None


@router.post("/documents")
async def create_document(form_data: CreateDocumentForm, user=Depends(get_verified_user)):
    return await _proxy_json("POST", "/api/v1/legal/documents", user, form_data.model_dump(exclude_none=True))


class PopulateForm(BaseModel):
    fields: dict


@router.post("/documents/{document_id}/populate")
async def populate_document(document_id: str, form_data: PopulateForm, user=Depends(get_verified_user)):
    return await _proxy_json("POST", f"/api/v1/legal/documents/{document_id}/populate", user, form_data.model_dump())


class UpdateDocumentForm(BaseModel):
    instruction: Optional[str] = None
    fieldValues: Optional[dict] = None


@router.patch("/documents/{document_id}")
async def update_document(document_id: str, form_data: UpdateDocumentForm, user=Depends(get_verified_user)):
    return await _proxy_json(
        "PATCH",
        f"/api/v1/legal/documents/{document_id}",
        user,
        form_data.model_dump(exclude_none=True),
    )


@router.post("/documents/{document_id}/validate")
async def validate_document(document_id: str, user=Depends(get_verified_user)):
    return await _proxy_json("POST", f"/api/v1/legal/documents/{document_id}/validate", user)


class RenderForm(BaseModel):
    format: str = "md"
    signatureCopy: bool = False


@router.post("/documents/{document_id}/render")
async def render_document(document_id: str, form_data: RenderForm, user=Depends(get_verified_user)):
    if form_data.format in ("docx", "pdf"):
        return await _proxy_binary(
            "POST",
            f"/api/v1/legal/documents/{document_id}/render",
            user,
            form_data.model_dump(),
        )
    return await _proxy_json(
        "POST",
        f"/api/v1/legal/documents/{document_id}/render",
        user,
        form_data.model_dump(),
    )


@router.get("/documents/{document_id}")
async def get_document(document_id: str, user=Depends(get_verified_user)):
    return await _proxy_json("GET", f"/api/v1/legal/documents/{document_id}", user)


@router.get("/documents")
async def search_documents(
    q: Optional[str] = None,
    templateId: Optional[str] = None,
    state: Optional[str] = None,
    readiness: Optional[str] = None,
    user=Depends(get_verified_user),
):
    params = {}
    if q:
        params["q"] = q
    if templateId:
        params["templateId"] = templateId
    if state:
        params["state"] = state
    if readiness:
        params["readiness"] = readiness

    path = "/api/v1/legal/documents"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    if query:
        path = f"{path}?{query}"

    return await _proxy_json("GET", path, user)


@router.post("/compare")
async def compare_document(
    file: UploadFile = File(...),
    templateId: Optional[str] = Form(default=None),
    user=Depends(get_verified_user),
):
    return await _proxy_multipart("/api/v1/legal/compare", user, file, template_id=templateId)


class CreatePackageForm(BaseModel):
    counterparty: dict
    answers: dict


@router.post("/packages")
async def create_package(form_data: CreatePackageForm, user=Depends(get_verified_user)):
    return await _proxy_json("POST", "/api/v1/legal/packages", user, form_data.model_dump())


@router.get("/packages/{package_id}")
async def get_package(package_id: str, user=Depends(get_verified_user)):
    return await _proxy_json("GET", f"/api/v1/legal/packages/{package_id}", user)


class StateTransitionForm(BaseModel):
    to: str


@router.post("/documents/{document_id}/state")
async def state_transition(document_id: str, form_data: StateTransitionForm, user=Depends(get_verified_user)):
    return await _proxy_json(
        "POST",
        f"/api/v1/legal/documents/{document_id}/state",
        user,
        form_data.model_dump(),
    )


@router.get("/documents/{document_id}/audit")
async def get_audit_trail(document_id: str, user=Depends(get_verified_user)):
    return await _proxy_json("GET", f"/api/v1/legal/documents/{document_id}/audit", user)
