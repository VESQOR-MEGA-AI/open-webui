"""VESQOR Mega AI signup geo gate (2026-08-28, A.9; fail-closed 2026-09-11).

Restricts signup to an allow-list of countries (default: US) resolved from the
requesting IP. The decision is a pure function over the resolved country so the
gate can be unit-tested without a live geolocation service.

Policy (owner decision 2026-09-11): FAIL-CLOSED. If the geolocation service is
unreachable, returns a non-200, or yields no country, the signup is REFUSED
(403, "try later") rather than silently allowed. Rationale: this gate is a
jurisdictional control (US-only signup), not a censorship system — failing open
would let a jurisdiction control be bypassed by DoS-ing the lookup or by
egressing from an unexpected IP. The operator can re-attempt once the lookup
service recovers.

The first admin signup (no users yet) is exempt — the operator bootstraps the
instance before any policy applies. That exemption lives at the call site.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

GEO_LOOKUP_URL = "https://ipapi.co/{ip}/country/"
GEO_LOOKUP_TIMEOUT = 5

DENY_OUT_OF_REGION = "Signup is currently available in the United States only."
DENY_LOOKUP_UNAVAILABLE = (
    "Signup is temporarily unavailable (region could not be verified). "
    "Please try again later."
)


def parse_allowed_countries(raw: str) -> set[str]:
    """Parse a comma/space separated country allow-list into upper-case codes."""
    if not raw:
        return set()
    return {c.strip().upper() for c in raw.replace(',', ' ').split() if c.strip()}


def decide_signup_country(country: str | None, allowed: set[str]) -> tuple[bool, str | None]:
    """Pure decision: (allowed, denial_reason).

    country=None means the lookup did not produce a country (service down,
    non-200, empty body) -> fail-closed denial.
    """
    if not allowed:
        return True, None
    if not country:
        return False, DENY_LOOKUP_UNAVAILABLE
    if country not in allowed:
        return False, DENY_OUT_OF_REGION
    return True, None


async def resolve_country(client_ip: str) -> str | None:
    """Resolve an IP to an upper-case ISO country code. None on any failure."""
    if not client_ip:
        return None
    # Imported lazily so the decision helpers above stay importable (and
    # unit-testable) without the aiohttp runtime dependency present.
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=GEO_LOOKUP_TIMEOUT)) as session:
            async with session.get(GEO_LOOKUP_URL.format(ip=client_ip)) as resp:
                if resp.status != 200:
                    log.warning('Geo lookup for %s returned HTTP %s', client_ip, resp.status)
                    return None
                body = (await resp.text()).strip().upper()
                if not body or len(body) > 3:
                    log.warning('Geo lookup for %s returned unusable body %r', client_ip, body[:40])
                    return None
                return body
    except Exception as e:  # network error, timeout, malformed response
        log.warning('Geo lookup failed for %s: %s', client_ip, e)
        return None


async def check_signup_country(client_ip: str, allowed_raw: str) -> tuple[bool, str | None]:
    """Full gate: resolve the caller's country and apply the allow-list.

    Returns (allowed, denial_reason). Fail-closed: an unresolvable country is a
    denial when an allow-list is configured.
    """
    allowed = parse_allowed_countries(allowed_raw)
    if not allowed:
        return True, None
    country = await resolve_country(client_ip)
    allowed_decision, reason = decide_signup_country(country, allowed)
    if allowed_decision:
        log.info('Signup geo gate: ip=%s country=%s allowed', client_ip, country)
    else:
        log.warning('Signup geo gate DENIED: ip=%s country=%s allowed=%s', client_ip, country, sorted(allowed))
    return allowed_decision, reason
