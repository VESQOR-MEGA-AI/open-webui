"""VESQOR Mega AI embryo screening (2026-08-28).

The brain's compliance engine (OFAC/UK/EU/UN sanctions lists, deterministic,
zero-LLM) screens every signup embryo BEFORE it is born. A critical match
means the embryo is rejected — the account is never created.

Fail-open: if the brain is unreachable, the signup is ALLOWED (never block
legitimate users on an outage). The brain's screen endpoint is deterministic
and cheap; this is a compliance gate, not a censorship system.
"""

import logging

import aiohttp

from open_webui.env import VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN

log = logging.getLogger(__name__)

_CRITICAL_LEVELS = {"critical"}


async def screen_embryo(name: str, company: str | None = None) -> tuple[bool, str | None, list]:
    """Screen a signup embryo against the brain's sanctions engine.

    Returns (allowed, level, matches):
      - allowed=False when the brain returned a critical match (embryo rejected)
      - allowed=True otherwise (no match, non-critical match, or brain unreachable)
    """
    if not VESQOR_SERVICE_TOKEN:
        return True, None, []

    payload = {"name": name}
    if company:
        payload["jurisdiction"] = company

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(
                f"{VESQOR_API_BASE_URL.rstrip('/')}/api/v1/compliance/screen",
                json=payload,
                headers={"Authorization": f"Bearer {VESQOR_SERVICE_TOKEN}"},
            ) as resp:
                if resp.status != 200:
                    log.warning("Compliance screen returned %s; allowing (fail-open)", resp.status)
                    return True, None, []
                data = await resp.json()
    except Exception as e:
        log.warning("Compliance screen failed (%s); allowing (fail-open)", e)
        return True, None, []

    level = data.get("level")
    matches = data.get("matches") or []
    if level in _CRITICAL_LEVELS:
        log.warning("Embryo rejected by compliance screen: level=%s matches=%s", level, matches)
        return False, level, matches
    return True, level, matches
