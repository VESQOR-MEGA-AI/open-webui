"""VQ-25: environment-driven configuration for the three compared providers.

Three providers, one shape: each is configured by exactly three explicit
variables — ``ANSWER_COMPARE_<PROVIDER>_BASE_URL``, ``_API_KEY``, ``_MODEL`` —
and **nothing falls back to anything**. All three endpoints are OpenAI-compatible,
including the VESQOR engine's door, so there is no provider special case here.

The no-fallback rule is load-bearing, not tidiness. ``OPENAI_API_KEY`` may hold
the VESQOR agent token on this deployment (``docs/spec-vesqor-integration.md``
describes the engine as an OpenAI-compatible door reached by repointing
``OPENAI_*``), so falling back to it could ship that token to ``api.openai.com``.
And a ``vesqor`` provider falling back to ``OPENAI_*`` that someone later
repointed at real OpenAI would make the page compare ChatGPT against itself under
a column labelled VESQOR — a failure that looks exactly like success. Unset means
honestly unconfigured. ``GEMINI_API_KEY`` is unambiguous and carries no such risk;
it is dropped anyway, because an implicit fallback is what produced both mistakes
and "no exceptions" is the only version of this rule a later reader cannot
misapply.

``VESQOR_SERVICE_TOKEN`` / ``VESQOR_API_BASE_URL`` are deliberately not used here:
they authenticate the billing/admin proxy in ``routers/vesqor.py``, a different
surface from the engine's chat door.

Every value is read from ``os.environ`` **at call time**, not at import time:
``config.py`` reads ``OPENAI_API_KEY``/``GEMINI_API_KEY`` into plain module
constants while the module is being imported, which makes that style impossible
to exercise from a test. This module is the seam the tests monkeypatch.

Nothing here ever returns or logs key material. Base URLs are returned with the
query string and any user-info component stripped: Gemini-compatible endpoints
accept a ``?key=`` parameter, so a raw base URL can carry a secret.
"""

import logging
import os
from typing import NamedTuple, Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

log = logging.getLogger(__name__)

PROVIDER_CHATGPT = 'chatgpt'
PROVIDER_GEMINI = 'gemini'
PROVIDER_VESQOR = 'vesqor'

# Fixed order — the frontend and later stages rely on these literals.
PROVIDER_IDS: tuple[str, ...] = (PROVIDER_CHATGPT, PROVIDER_GEMINI, PROVIDER_VESQOR)

ENV_CHATGPT_BASE_URL = 'ANSWER_COMPARE_CHATGPT_BASE_URL'
ENV_CHATGPT_API_KEY = 'ANSWER_COMPARE_CHATGPT_API_KEY'
ENV_CHATGPT_MODEL = 'ANSWER_COMPARE_CHATGPT_MODEL'

ENV_GEMINI_BASE_URL = 'ANSWER_COMPARE_GEMINI_BASE_URL'
ENV_GEMINI_API_KEY = 'ANSWER_COMPARE_GEMINI_API_KEY'
ENV_GEMINI_MODEL = 'ANSWER_COMPARE_GEMINI_MODEL'

ENV_VESQOR_BASE_URL = 'ANSWER_COMPARE_VESQOR_BASE_URL'
ENV_VESQOR_API_KEY = 'ANSWER_COMPARE_VESQOR_API_KEY'
ENV_VESQOR_MODEL = 'ANSWER_COMPARE_VESQOR_MODEL'

DEFAULT_CHATGPT_BASE_URL = 'https://api.openai.com/v1'
# Gemini's OpenAI-compatible endpoint.
DEFAULT_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai'
# No default for the VESQOR door: its URL differs per deployment, and guessing
# one would point the engine column at whatever happens to answer there.
DEFAULT_VESQOR_BASE_URL = None

# A cheap pre-flight guard, in CHARACTERS, not a context window. It is ours, not
# the provider's: exceeding it is refused before anything is sent, while the real
# context limit still lives upstream and comes back as a context_length_exceeded
# error. The two must always reach the user as distinct, accurate messages.
# Deliberately not part of the credential trio above — it has a default, so it is
# never something the page reports as missing.
DEFAULT_MAX_INPUT_CHARS = 100_000
ENV_MAX_INPUT_CHARS_TEMPLATE = 'ANSWER_COMPARE_{provider}_MAX_INPUT_CHARS'

# A judge reads the prompt, the reference and up to three answers plus the
# scaffolding, so the generation limit — sized for one prompt — would refuse
# judging exactly where generation succeeded. Its own limit defaults to a
# multiple of the generation one.
JUDGE_MAX_INPUT_CHARS_MULTIPLIER = 4
ENV_JUDGE_MAX_INPUT_CHARS_TEMPLATE = 'ANSWER_COMPARE_{provider}_JUDGE_MAX_INPUT_CHARS'


class ProviderConfig(BaseModel):
    """What the admin page may know about a provider. Never carries key material."""

    id: str
    configured: bool
    missing: list[str]
    base_url: Optional[str] = None
    model: Optional[str] = None


def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


def _sanitize_base_url(url: str) -> Optional[str]:
    """Rebuild a base URL as ``scheme://host[:port]/path``, or return ``None``.

    A base URL is only mostly non-secret: ``?key=`` and ``user:pass@`` both
    smuggle credentials, and this value is returned to the client and logged.

    This fails closed on purpose. Anything that does not parse into the shape
    above — no scheme, no netloc, a malformed IPv6 literal, a non-numeric or
    out-of-range port — yields ``None`` and the caller reports the variable as
    still required. A lenient "strip what I can, pass the rest through" fallback
    is how ``user:TOKEN@host`` reaches the response body, and letting the
    ``ValueError`` escape would turn one typo in one variable into an HTTP 500
    for the whole configuration endpoint.
    """
    if not url:
        return None

    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return None
        host = parts.hostname
        port = parts.port  # parsed lazily; raises ValueError on a bad port
    except ValueError:
        return None

    if not host:
        return None

    # urlsplit strips the brackets off an IPv6 literal; put them back.
    netloc = f'[{host}]' if ':' in host else host
    if port is not None:
        netloc = f'{netloc}:{port}'

    return urlunsplit((parts.scheme, netloc, parts.path, '', ''))


class _ProviderEnv(NamedTuple):
    """The three variables a provider is configured by, plus an optional default URL."""

    base_url_env: str
    default_base_url: Optional[str]
    key_env: str
    model_env: str


_PROVIDER_ENV: dict[str, _ProviderEnv] = {
    PROVIDER_CHATGPT: _ProviderEnv(
        ENV_CHATGPT_BASE_URL,
        DEFAULT_CHATGPT_BASE_URL,
        ENV_CHATGPT_API_KEY,
        ENV_CHATGPT_MODEL,
    ),
    PROVIDER_GEMINI: _ProviderEnv(
        ENV_GEMINI_BASE_URL,
        DEFAULT_GEMINI_BASE_URL,
        ENV_GEMINI_API_KEY,
        ENV_GEMINI_MODEL,
    ),
    PROVIDER_VESQOR: _ProviderEnv(
        ENV_VESQOR_BASE_URL,
        DEFAULT_VESQOR_BASE_URL,
        ENV_VESQOR_API_KEY,
        ENV_VESQOR_MODEL,
    ),
}


def resolve_provider(provider_id: str) -> ProviderConfig:
    spec = _PROVIDER_ENV.get(provider_id)
    if spec is None:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')

    missing: list[str] = []

    if not _env(spec.key_env):
        missing.append(spec.key_env)

    model = _env(spec.model_env)
    if not model:
        # No invented default: guessing a model id that may not exist is the
        # fabricated-output failure the ticket forbids.
        missing.append(spec.model_env)

    base_url = _sanitize_base_url(_env(spec.base_url_env) or spec.default_base_url or '')
    if base_url is None:
        missing.append(spec.base_url_env)

    return ProviderConfig(
        id=provider_id,
        configured=not missing,
        missing=missing,
        base_url=base_url,
        model=model or None,
    )


def resolve_providers() -> list[ProviderConfig]:
    return [resolve_provider(provider_id) for provider_id in PROVIDER_IDS]


def log_startup_configuration() -> None:
    """Log the answer-compare provider configuration once, at application start.

    This is a report, not a gate: it must never raise and never prevent boot.
    Production today runs with only the ``vesqor`` provider configured —
    ``chatgpt`` and ``gemini`` are intentionally unset — so a fail-closed check
    here would refuse to start the whole chat application over a benchmark
    page most requests never touch. Unlike ``_assert_signup_gates()`` in
    ``main.py``, a misconfigured or partially-configured comparison provider
    degrades one admin feature; it is not a compliance control silently gone
    missing, so it only ever warns.

    Only variable *names*, the sanitised base URL, and the model id are ever
    logged — never key material — same rule as ``ProviderConfig`` itself.
    """
    configured_count = 0
    for provider in resolve_providers():
        if provider.configured:
            configured_count += 1
            log.info(
                'Answer-compare provider %r is configured (model=%s, base_url=%s)',
                provider.id,
                provider.model,
                provider.base_url,
            )
        else:
            log.warning(
                'Answer-compare provider %r is not configured — missing env var(s): %s',
                provider.id,
                ', '.join(provider.missing),
            )

    total = len(PROVIDER_IDS)
    if configured_count == 0:
        log.warning(
            'Answer-compare: 0/%d providers configured — the comparison benchmark has no usable provider yet.',
            total,
        )
    else:
        log.info('Answer-compare: %d/%d providers configured.', configured_count, total)


def resolve_api_key(provider_id: str) -> str:
    """The provider's key, read at call time.

    Deliberately a separate function rather than a field on ``ProviderConfig``:
    that object is serialised straight to the admin page, so a key must never be
    able to ride along in it. The caller passes this into one Authorization
    header and keeps it nowhere else.
    """
    spec = _PROVIDER_ENV.get(provider_id)
    if spec is None:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')
    return _env(spec.key_env)


def max_input_chars_env(provider_id: str) -> str:
    return ENV_MAX_INPUT_CHARS_TEMPLATE.format(provider=provider_id.upper())


def _positive_int_env(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning('%s is not an integer, using the default limit', name)
        return default
    if value <= 0:
        log.warning('%s must be positive, using the default limit', name)
        return default
    return value


def resolve_max_input_chars(provider_id: str) -> int:
    """The configured pre-flight input limit for generation, in characters."""
    if provider_id not in _PROVIDER_ENV:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')
    return _positive_int_env(max_input_chars_env(provider_id), DEFAULT_MAX_INPUT_CHARS)


def judge_max_input_chars_env(provider_id: str) -> str:
    return ENV_JUDGE_MAX_INPUT_CHARS_TEMPLATE.format(provider=provider_id.upper())


def resolve_judge_max_input_chars(provider_id: str) -> int:
    """The pre-flight input limit for judging, in characters.

    Defaults to a multiple of that provider's generation limit, so raising one
    raises the other unless the judge limit is set explicitly.
    """
    if provider_id not in _PROVIDER_ENV:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')
    default = resolve_max_input_chars(provider_id) * JUDGE_MAX_INPUT_CHARS_MULTIPLIER
    return _positive_int_env(judge_max_input_chars_env(provider_id), default)
