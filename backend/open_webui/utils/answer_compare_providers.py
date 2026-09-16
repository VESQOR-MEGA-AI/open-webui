"""VQ-25: environment-driven configuration for the compared providers.

Two roles, not one list. ``GENERATOR_IDS`` are the **candidates** — the systems
whose answers are compared against each other, shown as A/B/C. ``JUDGE_IDS`` is
the **adjudicator** — the one model that scores them. They are disjoint by
construction: the adjudicator never writes a candidate answer, and a candidate
never scores one, including its own. That disjointness is the whole meaning of
"independent adjudicator"; a provider appearing in both lists would be marking
its own homework, which is exactly the failure the blinding machinery in
``answer_compare_judge`` was built to make visible rather than to permit.

Every provider, in either role, is configured by exactly three explicit
variables — ``ANSWER_COMPARE_<PROVIDER>_BASE_URL``, ``_API_KEY``, ``_MODEL`` —
and **nothing falls back to anything**. All endpoints are OpenAI-compatible,
including the VESQOR engine's door and the gateway the adjudicator is reached
through, so there is no provider special case here.

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

Judge capability is **structural, not configuration**. There is no
``ANSWER_COMPARE_<PROVIDER>_CAN_JUDGE`` variable any more: which provider
adjudicates is a property of the design — one canonical engine, one independent
adjudicator — and an environment variable that could hand the role back to a
candidate would silently undo that independence on a single deployment, with no
code review and no visible difference on the page.

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
PROVIDER_ANTHROPIC = 'anthropic'

# The candidates, in fixed order — the frontend and later stages rely on these
# literals and on this order. These three generate the answers that are compared.
GENERATOR_IDS: tuple[str, ...] = (PROVIDER_CHATGPT, PROVIDER_GEMINI, PROVIDER_VESQOR)

# The adjudicator: one independent model, outside the field it scores.
ADJUDICATOR_ID = PROVIDER_ANTHROPIC
JUDGE_IDS: tuple[str, ...] = (ADJUDICATOR_ID,)

# Every provider this module can configure, generators first. Code that means
# "a candidate" must say GENERATOR_IDS and code that means "an adjudicator" must
# say JUDGE_IDS; this list exists only for configuration and validation, where
# the question really is "is this a provider we know at all".
PROVIDER_IDS: tuple[str, ...] = GENERATOR_IDS + JUDGE_IDS

ENV_CHATGPT_BASE_URL = 'ANSWER_COMPARE_CHATGPT_BASE_URL'
ENV_CHATGPT_API_KEY = 'ANSWER_COMPARE_CHATGPT_API_KEY'
ENV_CHATGPT_MODEL = 'ANSWER_COMPARE_CHATGPT_MODEL'

ENV_GEMINI_BASE_URL = 'ANSWER_COMPARE_GEMINI_BASE_URL'
ENV_GEMINI_API_KEY = 'ANSWER_COMPARE_GEMINI_API_KEY'
ENV_GEMINI_MODEL = 'ANSWER_COMPARE_GEMINI_MODEL'

ENV_VESQOR_BASE_URL = 'ANSWER_COMPARE_VESQOR_BASE_URL'
ENV_VESQOR_API_KEY = 'ANSWER_COMPARE_VESQOR_API_KEY'
ENV_VESQOR_MODEL = 'ANSWER_COMPARE_VESQOR_MODEL'

ENV_ANTHROPIC_BASE_URL = 'ANSWER_COMPARE_ANTHROPIC_BASE_URL'
ENV_ANTHROPIC_API_KEY = 'ANSWER_COMPARE_ANTHROPIC_API_KEY'
ENV_ANTHROPIC_MODEL = 'ANSWER_COMPARE_ANTHROPIC_MODEL'

DEFAULT_CHATGPT_BASE_URL = 'https://api.openai.com/v1'
# Gemini's OpenAI-compatible endpoint.
DEFAULT_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai'
# No default for the VESQOR door: its URL differs per deployment, and guessing
# one would point the engine column at whatever happens to answer there.
DEFAULT_VESQOR_BASE_URL = None
# No default for the adjudicator either, and for a sharper reason than VESQOR's.
# This deployment reaches Claude through a third-party gateway, not through
# ``api.anthropic.com`` — and ``api.anthropic.com`` is not OpenAI-compatible at
# ``/v1/chat/completions`` anyway, so naming it here would be a default that
# cannot work. Guessing the gateway's URL instead would send the adjudication
# key, the prompt and every candidate answer to whatever host happened to be
# guessed. Unset stays honestly unconfigured; the page says which variable is
# missing.
DEFAULT_ANTHROPIC_BASE_URL = None

# The adjudicator model. Unlike a generator's model id this one is NOT a guess —
# it is the model the adjudication engine is specified against. It stays
# overridable because the gateway in front of it may namespace model ids
# (``anthropic/claude-sonnet-5`` and similar), which is a routing detail of the
# deployment, not a change of model.
DEFAULT_ANTHROPIC_MODEL = 'claude-sonnet-5'

# A cheap pre-flight guard, in CHARACTERS, not a context window. It is ours, not
# the provider's: exceeding it is refused before anything is sent, while the real
# context limit still lives upstream and comes back as a context_length_exceeded
# error. The two must always reach the user as distinct, accurate messages.
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
    # Structural, not configurable: true for the adjudicator, false for every
    # candidate. The page reads this to decide which provider gets the
    # adjudication control, so it must never be able to drift from JUDGE_IDS.
    can_judge: bool = False


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
    """The three variables a provider is configured by, plus optional defaults."""

    base_url_env: str
    default_base_url: Optional[str]
    key_env: str
    model_env: str
    default_model: Optional[str] = None


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
    PROVIDER_ANTHROPIC: _ProviderEnv(
        ENV_ANTHROPIC_BASE_URL,
        DEFAULT_ANTHROPIC_BASE_URL,
        ENV_ANTHROPIC_API_KEY,
        ENV_ANTHROPIC_MODEL,
        DEFAULT_ANTHROPIC_MODEL,
    ),
}


def resolve_can_judge(provider_id: str) -> bool:
    """Whether this provider adjudicates. Structural — there is no override.

    An unknown id raises rather than returning ``False``: a typo must surface as
    a bug here, not as a provider that quietly "cannot judge".
    """
    if provider_id not in _PROVIDER_ENV:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')
    return provider_id in JUDGE_IDS


def resolve_judge_ids() -> tuple[str, ...]:
    """The provider ids that adjudicate — the single independent adjudicator.

    Kept as a function returning a tuple, rather than call sites reading
    ``JUDGE_IDS`` directly, because every judges-walk in the tally, the summary
    and the router already goes through this one seam. One adjudicator today is
    a fact about the configuration, not an assumption any caller may bake in:
    nothing downstream may index ``[0]`` or assume a length.
    """
    return JUDGE_IDS


def resolve_provider(provider_id: str) -> ProviderConfig:
    spec = _PROVIDER_ENV.get(provider_id)
    if spec is None:
        raise ValueError(f'Unknown answer-compare provider: {provider_id}')

    missing: list[str] = []

    if not _env(spec.key_env):
        missing.append(spec.key_env)

    # A default model is only ever a *specified* one (the adjudicator's), never
    # a guess: inventing a model id for a generator is the fabricated-output
    # failure the ticket forbids, so those still report the variable as missing.
    model = _env(spec.model_env) or (spec.default_model or '')
    if not model:
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
        can_judge=resolve_can_judge(provider_id),
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

    # Called out separately from the count above, because it is a different
    # failure. An unconfigured candidate costs the page one column; an
    # unconfigured adjudicator costs it adjudication entirely — there is no
    # second judge to fall back to, by design.
    unconfigured_judges = [judge for judge in JUDGE_IDS if not resolve_provider(judge).configured]
    if unconfigured_judges:
        log.warning(
            'Answer-compare: the adjudicator (%s) is not configured — the Compare page can '
            'generate answers but cannot adjudicate them.',
            ', '.join(unconfigured_judges),
        )


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
