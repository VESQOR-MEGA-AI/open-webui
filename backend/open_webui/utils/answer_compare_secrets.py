"""VQ-25: where the adjudicator's API key comes from at runtime.

Two sources, in a fixed order, per provider:

1. ``ANSWER_COMPARE_<PROVIDER>_API_KEY`` — the value directly in the environment.
2. ``ANSWER_COMPARE_<PROVIDER>_KEY_VAULT_URL`` + ``_KEY_VAULT_SECRET`` — the name
   of a secret in an Azure Key Vault, fetched at call time.

The direct variable wins when both are set. That order is deliberate: the vault
is the normal path, and the direct variable is the break-glass one — a way to get
the page working again without waiting on Azure, which is worth having precisely
on the day the vault is the thing that is broken.

**The "is it configured" question never touches the network.** ``resolve_provider``
runs on every ``GET /config`` and every run payload, so it may only ask whether a
key *source* exists (``has_key_source``); the fetch itself (``resolve_key``) runs
only in the adjudication path. Wiring a vault round trip into the config endpoint
would make an admin page's load time depend on Azure, and a vault outage would
present as a broken page rather than as a failed adjudication.

Fetched values are cached in memory for ``ANSWER_COMPARE_KEY_VAULT_CACHE_TTL_SECONDS``
(default 5 minutes). Two consequences worth knowing: Key Vault is rate-limited and
an adjudication is not a rare event, so an uncached read per adjudication is a real
cost; and a key rotated in the vault takes up to one TTL to be picked up here.
Lower the TTL if that lag matters more than the request volume.

Nothing here logs, returns, or raises the secret value. ``SecretResolutionError``
messages name the vault and the secret *name* only — a message carrying the value
would reach the admin page and the application log, which is the whole failure
this module exists to avoid.
"""

import asyncio
import logging
import os
import time
from typing import NamedTuple, Optional

log = logging.getLogger(__name__)

ENV_KEY_VAULT_URL_TEMPLATE = 'ANSWER_COMPARE_{provider}_KEY_VAULT_URL'
ENV_KEY_VAULT_SECRET_TEMPLATE = 'ANSWER_COMPARE_{provider}_KEY_VAULT_SECRET'

ENV_CACHE_TTL_SECONDS = 'ANSWER_COMPARE_KEY_VAULT_CACHE_TTL_SECONDS'
DEFAULT_CACHE_TTL_SECONDS = 300

# The vault call is on the adjudication path, which already has a long read
# timeout of its own. This one stays short: a hanging vault must surface as a
# failed adjudication with a clear reason, not as a request that never returns.
VAULT_TIMEOUT_SECONDS = 15.0


class SecretResolutionError(Exception):
    """A key source is configured but the value could not be obtained.

    ``code`` is what the API and the page branch on. The message is written here
    and never contains key material.
    """

    code = 'secret_unavailable'

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class KeyVaultSource(NamedTuple):
    """Where a key lives, with no key in it."""

    vault_url: str
    secret_name: str


class _CacheEntry(NamedTuple):
    value: str
    expires_at: float


# provider_id -> entry. Holds key material in memory, which is unavoidable —
# it has to be in memory to go into a request header — but it is never written
# anywhere else.
_CACHE: dict[str, _CacheEntry] = {}

# One lock per provider, so a burst of adjudications on a cold cache makes one
# vault call rather than one per request.
_LOCKS: dict[str, asyncio.Lock] = {}


def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


def key_vault_url_env(provider_id: str) -> str:
    return ENV_KEY_VAULT_URL_TEMPLATE.format(provider=provider_id.upper())


def key_vault_secret_env(provider_id: str) -> str:
    return ENV_KEY_VAULT_SECRET_TEMPLATE.format(provider=provider_id.upper())


def key_vault_source(provider_id: str) -> Optional[KeyVaultSource]:
    """The configured vault location, or ``None``. Reads the environment only.

    Both variables are required together: a vault URL with no secret name names
    no secret, and a secret name with no vault names no vault. Half-configured
    reads as not configured rather than as an error, so the page reports the
    missing variable like any other.
    """
    vault_url = _env(key_vault_url_env(provider_id))
    secret_name = _env(key_vault_secret_env(provider_id))
    if not vault_url or not secret_name:
        return None
    return KeyVaultSource(vault_url=vault_url, secret_name=secret_name)


def has_key_source(provider_id: str, direct_key: str) -> bool:
    """Whether a key can be obtained at all — cheap, and never a network call."""
    return bool(direct_key) or key_vault_source(provider_id) is not None


def cache_ttl_seconds() -> float:
    raw = _env(ENV_CACHE_TTL_SECONDS)
    if not raw:
        return float(DEFAULT_CACHE_TTL_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        log.warning('%s is not a number, using the default cache TTL', ENV_CACHE_TTL_SECONDS)
        return float(DEFAULT_CACHE_TTL_SECONDS)
    if value < 0:
        log.warning('%s must not be negative, using the default cache TTL', ENV_CACHE_TTL_SECONDS)
        return float(DEFAULT_CACHE_TTL_SECONDS)
    return value


def clear_cache(provider_id: Optional[str] = None) -> None:
    """Drop cached values. Exposed for tests and for a post-rotation refresh."""
    if provider_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(provider_id, None)


async def _fetch_from_vault(source: KeyVaultSource) -> str:
    """One Key Vault read, through the async clients.

    The imports are function-local on purpose: ``azure-keyvault-secrets`` is only
    needed by a deployment that uses the vault, and a missing package must become
    this module's typed error at call time rather than an ImportError that stops
    the whole application from starting.

    The async clients, not the synchronous ones: this runs inside the request's
    event loop, and a blocking vault call there would stall every other request
    on the worker for the duration.
    """
    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient
    except ImportError as err:  # pragma: no cover - depends on the install
        raise SecretResolutionError(
            'Key Vault is configured but the azure-keyvault-secrets package is not installed.',
            code='secret_backend_missing',
        ) from err

    from azure.core.exceptions import (
        ClientAuthenticationError,
        HttpResponseError,
        ResourceNotFoundError,
    )

    # DefaultAzureCredential covers a managed identity on Azure and a service
    # principal (AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET)
    # anywhere else — which is what this application uses, since it does not run
    # on Azure. Both are closed in the finally, or the process leaks a session
    # per adjudication.
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=source.vault_url, credential=credential)
    try:
        secret = await asyncio.wait_for(client.get_secret(source.secret_name), timeout=VAULT_TIMEOUT_SECONDS)
    except TimeoutError as err:
        raise SecretResolutionError(
            f'Key Vault {source.vault_url} did not respond within {VAULT_TIMEOUT_SECONDS:.0f}s.',
            code='secret_timeout',
        ) from err
    except ClientAuthenticationError as err:
        # No `from err` detail in the message: an Azure auth error can echo the
        # request back, and this message reaches the admin page.
        raise SecretResolutionError(
            f'Could not authenticate to Key Vault {source.vault_url}. '
            'Check the service principal credentials available to the application.',
            code='secret_auth',
        ) from err
    except ResourceNotFoundError as err:
        raise SecretResolutionError(
            f'Secret {source.secret_name!r} was not found in Key Vault {source.vault_url}.',
            code='secret_not_found',
        ) from err
    except HttpResponseError as err:
        raise SecretResolutionError(
            f'Key Vault {source.vault_url} returned an error reading {source.secret_name!r} '
            f'(status {getattr(err, "status_code", None)}).',
            code='secret_unavailable',
        ) from err
    finally:
        await client.close()
        await credential.close()

    value = (secret.value or '').strip()
    if not value:
        raise SecretResolutionError(
            f'Secret {source.secret_name!r} in Key Vault {source.vault_url} is empty.',
            code='secret_empty',
        )
    return value


async def resolve_key(provider_id: str, direct_key: str) -> str:
    """The provider's key: the direct variable, or the cached vault value.

    Returns ``''`` when no source is configured at all — the caller reports that
    as "not configured", the same as an unset key has always been. Raises
    ``SecretResolutionError`` only when a source *is* configured and could not be
    read, which is a different thing and must read differently on the page.
    """
    if direct_key:
        return direct_key

    source = key_vault_source(provider_id)
    if source is None:
        return ''

    now = time.monotonic()
    entry = _CACHE.get(provider_id)
    if entry is not None and entry.expires_at > now:
        return entry.value

    lock = _LOCKS.setdefault(provider_id, asyncio.Lock())
    async with lock:
        # Re-check inside the lock: another request may have filled the cache
        # while this one waited, and the point of the lock is one vault call.
        entry = _CACHE.get(provider_id)
        now = time.monotonic()
        if entry is not None and entry.expires_at > now:
            return entry.value

        log.info(
            'Answer-compare: reading the %s key from Key Vault %s (secret %s)',
            provider_id,
            source.vault_url,
            source.secret_name,
        )
        value = await _fetch_from_vault(source)
        ttl = cache_ttl_seconds()
        if ttl > 0:
            _CACHE[provider_id] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)
        return value
