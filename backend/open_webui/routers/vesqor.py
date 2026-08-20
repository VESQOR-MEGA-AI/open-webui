import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.env import VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN
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
