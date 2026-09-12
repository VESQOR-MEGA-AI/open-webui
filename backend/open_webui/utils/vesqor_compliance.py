"""VESQOR Mega AI embryo screening (2026-08-28; domain screening + fail-closed 2026-09-11).

The brain's compliance engine (OFAC/UK/EU/UN sanctions lists, deterministic,
zero-LLM) screens every signup embryo BEFORE it is born. A critical match
means the embryo is rejected — the account is never created.

Two entities are screened, concurrently:
  1. the person's display name (sanctioned individuals)
  2. the registrable email domain, e.g. ``sberbank`` from ``x@sberbank.com``
     (sanctioned organisations)

WHY THE DOMAIN MATTERS (2026-09-11 regression): screening only ``user.name``
let ``vberking@sberbank.com`` through as "Basil berking" (level=low) when the
organisation screen of ``sberbank`` returns critical/100. Anybody can type a
neutral display name, so the domain is the stronger signal.

FAIL-CLOSED: if the screen cannot be performed (brain unreachable, non-200),
the signup is REJECTED rather than allowed. A sanctions gate that opens on
outage is not a gate. The brain is our own service and signup volume is low,
so the availability cost is negligible.

Free/consumer mailbox providers are skipped for domain screening: they are
not organisations and would only add noise.
"""

import asyncio
import logging

log = logging.getLogger(__name__)

_CRITICAL_LEVELS = {"critical"}

# Consumer mailbox providers — never screened as organisations.
_FREEMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "msn.com",
    "yahoo.com",
    "yahoo.co.uk",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "mail.com",
    "aol.com",
    "zoho.com",
    "yandex.ru",
    "yandex.com",
    "mail.ru",
    "inbox.ru",
    "list.ru",
    "bk.ru",
    "qq.com",
    "163.com",
    "126.com",
    "web.de",
    "t-online.de",
    "webmail.com",
    # own domains — screening them is pure noise
    "vesqor.com",
    "vesqorai.com",
    "vesqor.co",
}


def email_domain_candidates(email: str | None) -> list[str]:
    """Return the organisation names worth screening for a signup email.

    Pure function (no network) so it can be unit-tested in CI. Free mailbox
    providers and malformed addresses yield an empty list.
    """
    if not email or "@" not in email:
        return []

    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return []
    if domain in _FREEMAIL_DOMAINS:
        return []

    # Screen both the full domain and its registrable label: "mail.sberbank.com"
    # -> {"sberbank.com", "sberbank"}; the label catches lists that store the
    # bare organisation name.
    labels = [part for part in domain.split(".") if part]
    if len(labels) < 2:
        return []
    label = labels[-2]
    # "sberbank.co.uk" -> second-to-last is "co"; fall back one more when the
    # label is a public-suffix-style token ("co", "com").
    if label in {"co", "com", "org", "net", "gov", "edu", "ac"} and len(labels) >= 3:
        label = labels[-3]

    candidates = [domain]
    if label and label not in candidates:
        candidates.append(label)
    return candidates


def _runtime():
    """Import the app runtime lazily.

    Keeps the pure helpers above (``email_domain_candidates``) importable
    without aiohttp / the open_webui package, so the CI regression test can
    exercise them with stdlib only.
    """
    import aiohttp

    from open_webui.env import VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN

    return aiohttp, VESQOR_API_BASE_URL, VESQOR_SERVICE_TOKEN


async def _screen_one(session, entity: str) -> tuple[int, dict | None]:
    """POST a single entity to the brain screen endpoint.

    Returns (http_status, body) — body is None when the response was not
    parseable. Raises on transport failure.
    """
    _, api_base, service_token = _runtime()
    async with session.post(
        f"{api_base.rstrip('/')}/api/v1/compliance/screen",
        json={"name": entity},
        headers={"Authorization": f"Bearer {service_token}"},
    ) as resp:
        if resp.status != 200:
            return resp.status, None
        try:
            return 200, await resp.json()
        except Exception:  # noqa: BLE001 - unparseable body == unusable screen
            return 200, None


async def screen_embryo(
    name: str, company: str | None = None, email: str | None = None
) -> tuple[bool, str | None, list]:
    """Screen a signup embryo against the brain's sanctions engine.

    Screens the display name (and optional company), plus the email domain
    when it is a corporate domain.

    Returns (allowed, level, matches):
      - allowed=False on any critical match, OR when the screen could not be
        performed at all (fail-closed)
      - allowed=True only when every entity was screened and none was critical
    """
    aiohttp, _, service_token = _runtime()
    if not service_token:
        log.error("Compliance screen skipped: VESQOR_SERVICE_TOKEN is not configured (fail-closed)")
        return False, "unconfigured", []

    entities: list[str] = []
    if name:
        entities.append(name)
    if company:
        entities.append(company)
    entities.extend(email_domain_candidates(email))

    # De-duplicate, preserving order.
    seen: set[str] = set()
    entities = [e for e in entities if not (e in seen or seen.add(e))]

    if not entities:
        return True, None, []

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            results = await asyncio.gather(
                *[_screen_one(session, entity) for entity in entities],
                return_exceptions=True,
            )
    except Exception as e:  # noqa: BLE001
        log.error("Compliance screen failed (%s); rejecting signup (fail-closed)", e)
        return False, "unavailable", []

    for entity, result in zip(entities, results):
        if isinstance(result, BaseException):
            log.error(
                "Compliance screen failed for %r (%s); rejecting signup (fail-closed)",
                entity,
                result,
            )
            return False, "unavailable", []

        status, data = result
        if status != 200 or data is None:
            log.error(
                "Compliance screen returned %s for %r; rejecting signup (fail-closed)",
                status,
                entity,
            )
            return False, "unavailable", []

        level = data.get("level")
        matches = data.get("matches") or []
        if level in _CRITICAL_LEVELS:
            log.warning(
                "Embryo rejected by compliance screen: entity=%r level=%s matches=%s",
                entity,
                level,
                matches,
            )
            return False, level, matches

    return True, None, []
