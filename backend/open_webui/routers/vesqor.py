import asyncio
import logging
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from open_webui.config import LIBRARY_DIR
from open_webui.env import VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN
from open_webui.models.library import Library, LibraryItemForm
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT = httpx.Timeout(30.0)


def _service_headers(user) -> dict:
    return {
        "Authorization": f"Bearer {VESQOR_SERVICE_TOKEN}",
        "X-VESQOR-User-Email": user.email,
        "Content-Type": "application/json",
    }


async def _proxy(method: str, path: str, user, json_body: Optional[dict] = None):
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
        log.exception(f"VESQOR brain request failed: {e}")
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
        log.error(f"VESQOR brain returned {response.status_code}: {response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service error",
        )

    try:
        body = response.json()
    except ValueError:
        body = None

    return body, response.status_code


async def _proxy_raw(method: str, path: str, user, json_body: Optional[dict] = None, timeout: float = _TIMEOUT):
    """Proxy returning the raw (possibly binary) response, like the brain export endpoint."""
    if not VESQOR_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VESQOR integration not configured",
        )

    url = f"{VESQOR_API_BASE_URL.rstrip('/')}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers=_service_headers(user),
                json=json_body,
            )
    except httpx.RequestError as e:
        log.exception(f"VESQOR brain request failed: {e}")
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
        log.error(f"VESQOR brain returned {response.status_code}: {response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="VESQOR service error",
        )

    return response


def _require_admin(user):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


class CheckoutForm(BaseModel):
    variant: str


class CreditTopupForm(BaseModel):
    credits: int


class ExportForm(BaseModel):
    """Export a VESQOR report: either a persisted reportId or an ad-hoc title+body."""
    format: str = "md"
    reportId: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    chat_id: Optional[str] = None
    chat_title: Optional[str] = None
    message_id: Optional[str] = None


class ProviderForm(BaseModel):
    class Config:
        extra = "allow"


class TokenForm(BaseModel):
    class Config:
        extra = "allow"


############################
# Billing
############################


@router.get("/billing/status")
async def get_billing_status(user=Depends(get_verified_user)):
    body, _ = await _proxy("GET", "/api/billing/status", user)
    return body


@router.post("/checkout")
async def create_checkout(form_data: CheckoutForm, user=Depends(get_verified_user)):
    body, _ = await _proxy("POST", "/api/checkout", user, form_data.model_dump())
    return body


@router.post("/portal")
async def create_portal_session(user=Depends(get_verified_user)):
    body, _ = await _proxy("POST", "/api/portal", user)
    return body


@router.get("/credits")
async def get_credits(user=Depends(get_verified_user)):
    body, _ = await _proxy("GET", "/api/credits", user)
    return body


@router.post("/credits/topup")
async def create_credit_topup(form_data: CreditTopupForm, user=Depends(get_verified_user)):
    body, _ = await _proxy("POST", "/api/credits/topup", user, form_data.model_dump())
    return body


############################
# Report export
############################

_EXPORT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
    "md": "text/markdown",
    "json": "application/json",
}


@router.post("/export")
async def export_report(form_data: ExportForm, user=Depends(get_verified_user)):
    """Proxy the brain export engine (RPT-3): POST /api/v1/export on api.vesqorai.com.

    Accepts {reportId} OR {title, body}; format: pdf|docx|html|md|json.
    Returns the generated file to the client.
    """
    fmt = (form_data.format or "md").lower()
    if fmt not in _EXPORT_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export format: {fmt}",
        )

    if not form_data.reportId and not (form_data.title and form_data.body):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either reportId or title+body must be provided",
        )

    brain_payload = form_data.model_dump(exclude={"chat_id", "chat_title", "message_id"})
    response = await _proxy_raw("POST", "/api/v1/export", user, brain_payload, timeout=120.0)
    content = response.content
    content_type = response.headers.get("content-type", _EXPORT_CONTENT_TYPES[fmt]).split(";")[0].strip()

    if response.status_code < 300 and content:
        try:
            safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", (form_data.title or form_data.reportId or "report")).strip("_") or "report"
            stored_filename = f"{safe_title}.{fmt}"
            stored_path = LIBRARY_DIR / f"{uuid.uuid4()}_{stored_filename}"
            await asyncio.to_thread(stored_path.write_bytes, content)

            await Library.insert_new_item(
                user.id,
                LibraryItemForm(
                    chat_id=form_data.chat_id,
                    chat_title=form_data.chat_title,
                    message_id=form_data.message_id,
                    filename=stored_filename,
                    content_type=content_type,
                    size=len(content),
                    format=fmt,
                    source="report_export",
                    path=str(stored_path),
                ),
            )
        except Exception as e:
            log.warning(f"Failed to save exported report to library: {e}")

    return Response(
        content=content,
        status_code=response.status_code,
        media_type=content_type,
        headers={"Content-Disposition": response.headers.get("content-disposition", "")},
    )


############################
# Admin: Providers
############################


@router.get("/admin/providers")
async def get_providers(user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("GET", "/api/admin/providers", user)
    return body


@router.post("/admin/providers")
async def add_provider(form_data: ProviderForm, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("POST", "/api/admin/providers", user, form_data.model_dump())
    return body


@router.patch("/admin/providers/{id}")
async def update_provider(id: str, form_data: ProviderForm, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("PATCH", f"/api/admin/providers/{id}", user, form_data.model_dump())
    return body


@router.delete("/admin/providers/{id}")
async def delete_provider(id: str, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("DELETE", f"/api/admin/providers/{id}", user)
    return body


@router.post("/admin/providers/{id}/test")
async def test_provider(id: str, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("POST", f"/api/admin/providers/{id}/test", user)
    return body


############################
# Admin: Tokens
############################


@router.get("/admin/tokens")
async def get_tokens(user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("GET", "/api/admin/tokens", user)
    return body


@router.post("/admin/tokens")
async def create_token(form_data: TokenForm, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("POST", "/api/admin/tokens", user, form_data.model_dump())
    return body


@router.patch("/admin/tokens/{id}")
async def update_token(id: str, form_data: TokenForm, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("PATCH", f"/api/admin/tokens/{id}", user, form_data.model_dump())
    return body


@router.delete("/admin/tokens/{id}")
async def delete_token(id: str, user=Depends(get_verified_user)):
    _require_admin(user)
    body, _ = await _proxy("DELETE", f"/api/admin/tokens/{id}", user)
    return body
