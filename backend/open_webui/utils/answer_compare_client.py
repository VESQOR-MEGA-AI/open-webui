"""VQ-25: one non-streaming chat completion against an OpenAI-compatible endpoint.

All three compared providers — ChatGPT, Gemini's OpenAI-compatible endpoint and
the VESQOR engine's door — speak the same protocol, so they are called by the
same code with the **same payload**. That is a ticket requirement, not a
convenience: the comparison is only meaningful if nothing about the request
differs per provider, so there is no provider-specific system prompt and no
per-provider tailoring of any kind anywhere in this module.

``routers/openai.py`` is deliberately not reused: it is bound to the app's model
registry, per-user configuration and the VESQOR seal, none of which belong to an
admin benchmark.

Key material never leaves this module: it goes into one Authorization header and
is never logged, never put into an exception message, and never returned.
"""

import logging
import os
import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Reasoning models are slow (PLAN §4.1), so the read timeout is generous while
# the connect timeout stays short — a refused connection should fail fast.
# It must stay BELOW any proxy timeout in front of this server, otherwise the
# caller sees a dropped connection instead of the typed 'timeout' error below.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
ENV_REQUEST_TIMEOUT_SECONDS = 'ANSWER_COMPARE_REQUEST_TIMEOUT_SECONDS'

# Headers a VESQOR-ish deployment may use to report the engine build. Checked in
# order; the first present, non-empty one wins. Absent from all of them means the
# provider did not report a version — which is recorded as None, never invented.
ENGINE_VERSION_HEADERS = (
    'x-vesqor-engine-version',
    'x-engine-version',
    'x-vesqor-version',
    'x-model-version',
)

# Body objects that may carry the same thing, as {object: key} pairs.
ENGINE_VERSION_BODY_PATHS = (
    ('vq_meta', 'engine_version'),
    ('vq_meta', 'version'),
    ('vesqor', 'engine_version'),
    ('metadata', 'engine_version'),
)

REFERENCE_BLOCK_HEADER = 'Reference material:'
REFERENCE_BLOCK_OPEN = '<<<REFERENCE'
REFERENCE_BLOCK_CLOSE = 'REFERENCE>>>'


def request_timeout_seconds() -> float:
    """The configured read timeout, read from the environment at call time."""
    raw = (os.environ.get(ENV_REQUEST_TIMEOUT_SECONDS) or '').strip()
    if not raw:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning('%s is not a number, using the default timeout', ENV_REQUEST_TIMEOUT_SECONDS)
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    if value <= 0:
        log.warning('%s must be positive, using the default timeout', ENV_REQUEST_TIMEOUT_SECONDS)
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return value


class ProviderCallError(Exception):
    """A typed failure of one provider call.

    ``code`` is what the API and the page branch on; ``message`` is written by
    this module and never contains key material or a raw upstream body.

    ``status`` is the HTTP status when there was one, so a caller can tell a
    rejected request (400) from everything else. ``upstream_message`` is a short,
    key-scrubbed excerpt of what the provider said — diagnostics for the
    structured-output ladder, never shown as the user-facing message.
    """

    code = 'upstream_error'

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        status: Optional[int] = None,
        upstream_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.upstream_message = upstream_message
        if code is not None:
            self.code = code


class AuthError(ProviderCallError):
    code = 'auth'


class RateLimitError(ProviderCallError):
    code = 'rate_limit'


class ContextLengthExceededError(ProviderCallError):
    """The upstream context window was exceeded.

    Its own class on purpose: this is the case the pre-flight character check
    cannot catch, and the page must be able to say precisely that, rather than
    showing a generic bad-request.
    """

    code = 'context_length_exceeded'


class UpstreamError(ProviderCallError):
    code = 'upstream_error'


class NetworkError(ProviderCallError):
    code = 'network'


class TimeoutError_(ProviderCallError):
    code = 'timeout'


class MalformedResponseError(ProviderCallError):
    code = 'malformed_response'


class TruncatedError(ProviderCallError):
    """The provider stopped at its output limit before finishing.

    Its own code on purpose: a report cut off by ``max_tokens`` would otherwise
    reach the parser as broken JSON and be filed as ``malformed_report``, which
    reads as "the model cannot follow the schema" when the truth is "we gave it
    too few tokens". Retryable.

    ``params`` carries what the call recorded before it was cut off — in
    particular ``output_tokens`` — so the failed row still answers "was the
    limit the problem?".
    """

    code = 'truncated'

    def __init__(self, message: str, params: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.params = params


class ProviderAnswer(BaseModel):
    """What one successful call produced."""

    text: str
    # The model id the provider reports, which may differ from the one requested
    # (providers resolve aliases to dated builds).
    model: Optional[str] = None
    engine_version: Optional[str] = None
    params: dict


def build_messages(prompt: str, reference: Optional[str]) -> list[dict[str, str]]:
    """The one message every provider receives, byte for byte.

    No system prompt: an instruction added here would be an input none of the
    providers were asked to compare on, and a per-provider one would invalidate
    the comparison outright.
    """
    if reference and reference.strip():
        content = f'{REFERENCE_BLOCK_HEADER}\n{REFERENCE_BLOCK_OPEN}\n{reference}\n{REFERENCE_BLOCK_CLOSE}\n\n{prompt}'
    else:
        content = prompt

    return [{'role': 'user', 'content': content}]


def _extract_engine_version(response: httpx.Response, body: dict[str, Any]) -> Optional[str]:
    """The provider's own engine/build version, or None when it reports none.

    Never invented: a missing version is recorded as missing.
    """
    for header in ENGINE_VERSION_HEADERS:
        value = (response.headers.get(header) or '').strip()
        if value:
            return value

    for container, key in ENGINE_VERSION_BODY_PATHS:
        obj = body.get(container)
        if isinstance(obj, dict):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _looks_like_context_length(body: dict[str, Any]) -> bool:
    error = body.get('error')
    if not isinstance(error, dict):
        return False
    code = error.get('code')
    if isinstance(code, str) and code.strip().lower() == 'context_length_exceeded':
        return True
    message = error.get('message')
    return isinstance(message, str) and 'context length' in message.lower()


# How much of a provider's error text is kept for diagnostics.
UPSTREAM_MESSAGE_MAX_CHARS = 300
_BEARER_RE = re.compile(r'Bearer\s+\S+', re.I)


def scrub_secret(text: str, api_key: str) -> str:
    """Remove the key — and any bearer token — from provider text before it is kept.

    A 400 body may echo the request headers back; that text is stored and logged.
    """
    if not text:
        return text
    scrubbed = text.replace(api_key, '[redacted]') if api_key else text
    return _BEARER_RE.sub('Bearer [redacted]', scrubbed)


def _upstream_message(response: httpx.Response, body: dict[str, Any], api_key: str) -> str:
    """A short excerpt of what the provider said, safe to store."""
    error = body.get('error')
    text: str = ''
    if isinstance(error, dict) and isinstance(error.get('message'), str):
        text = error['message']
    elif isinstance(error, str):
        text = error
    else:
        text = response.text or ''
    return scrub_secret(text, api_key)[:UPSTREAM_MESSAGE_MAX_CHARS]


def _raise_for_status(response: httpx.Response, api_key: str = '') -> None:
    """Translate an HTTP failure into a typed error. The user-facing message never
    echoes the body; the scrubbed excerpt rides along as ``upstream_message``."""
    status = response.status_code
    if status < 400:
        return

    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    upstream = _upstream_message(response, body, api_key)

    if status in (401, 403):
        raise AuthError(
            f'The provider rejected our credentials (HTTP {status}).', status=status, upstream_message=upstream
        )
    if status == 429:
        raise RateLimitError(
            'The provider rate-limited the request (HTTP 429).', status=status, upstream_message=upstream
        )
    if status in (400, 413, 422) and _looks_like_context_length(body):
        raise ContextLengthExceededError(
            "The input exceeded the provider's context window.", status=status, upstream_message=upstream
        )
    raise UpstreamError(f'The provider returned HTTP {status}.', status=status, upstream_message=upstream)


def _extract_text(body: dict[str, Any]) -> str:
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices:
        raise MalformedResponseError('The provider returned no choices.')

    message = choices[0].get('message') if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise MalformedResponseError('The provider returned a choice without a message.')

    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise MalformedResponseError('The provider returned an empty answer.')

    return content


def _build_async_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """The single place an HTTP client is constructed.

    Also the seam tests replace to install a transport double — the application
    never has a way to fabricate an answer, so a double must enter here.
    """
    return httpx.AsyncClient(timeout=timeout)


class CompletionResult(BaseModel):
    """One completion, before any caller-specific interpretation."""

    content: str
    model: Optional[str] = None
    engine_version: Optional[str] = None
    # The non-message, non-model body fields actually sent.
    params: dict


async def chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    extra_body: Optional[dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> CompletionResult:
    """One non-streaming chat completion. Raises a ProviderCallError subclass on failure.

    ``extra_body`` is merged into the request body after ``stream: false`` and is
    returned verbatim as ``params`` — that is the record of what was sent.
    ``client`` exists so tests can inject a transport; the application always
    lets this function build its own.
    """
    url = f'{base_url.rstrip("/")}/chat/completions'
    params: dict[str, Any] = {'stream': False, **(extra_body or {})}
    payload = {
        'model': model,
        'messages': messages,
        **params,
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    timeout = httpx.Timeout(request_timeout_seconds(), connect=DEFAULT_CONNECT_TIMEOUT_SECONDS)

    owns_client = client is None
    http = client or _build_async_client(timeout)
    try:
        try:
            response = await http.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise TimeoutError_('The provider did not respond in time.') from None
        except httpx.HTTPError as err:
            # The exception text can carry the full request URL; the message is
            # written here instead so nothing from the request escapes.
            log.warning('answer-compare provider request failed: %s', type(err).__name__)
            raise NetworkError('Could not reach the provider.') from None

        _raise_for_status(response, api_key)

        try:
            body = response.json()
        except ValueError:
            raise MalformedResponseError('The provider returned a body that is not JSON.') from None
        if not isinstance(body, dict):
            raise MalformedResponseError('The provider returned a body that is not an object.')

        reported_model = body.get('model')
        return CompletionResult(
            content=_extract_text(body),
            model=reported_model if isinstance(reported_model, str) and reported_model.strip() else None,
            engine_version=_extract_engine_version(response, body),
            params=params,
        )
    finally:
        if owns_client:
            await http.aclose()


async def generate(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    reference: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> ProviderAnswer:
    """One answer to the shared prompt. Raises a ProviderCallError subclass on failure."""
    result = await chat_completion(
        base_url,
        api_key,
        model,
        build_messages(prompt, reference),
        client=client,
    )
    return ProviderAnswer(
        text=result.content,
        model=result.model,
        engine_version=result.engine_version,
        params=result.params,
    )
