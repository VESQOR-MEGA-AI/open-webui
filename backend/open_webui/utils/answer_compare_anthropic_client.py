"""VQ-25: one non-streaming completion against an Anthropic Messages endpoint.

The independent judge (``sonnet``) is reached through the Anthropic ``/v1/messages``
format — via Kie.ai by default, or Anthropic directly. This module produces the
**same** ``CompletionResult`` the OpenAI client produces, so the judge core and
the ladder above it do not know which door they talked to; the only thing that
differs is the request body and how the answer is dug out of the response.

Everything that is shared is imported from the OpenAI client rather than copied:
the timeout resolution, the HTTP client seam tests replace, the typed error
classes, and the key scrubbing. Key material goes into one Authorization header
and nowhere else.
"""

import logging
import os
from typing import Any, Optional

import httpx

from open_webui.utils import answer_compare_client as openai_client
from open_webui.utils.answer_compare_client import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    UPSTREAM_MESSAGE_MAX_CHARS,
    AuthError,
    CompletionResult,
    ContextLengthExceededError,
    MalformedResponseError,
    NetworkError,
    RateLimitError,
    TimeoutError_,
    TruncatedError,
    UpstreamError,
    request_timeout_seconds,
    scrub_secret,
)

log = logging.getLogger(__name__)

ANTHROPIC_VERSION = '2023-06-01'

# The Messages API requires max_tokens. Live check on Kie (LIVE-CHECK.md,
# 2026-09-15): identical judge requests succeed at 2048 and 4096 (~50 s, 1118 and
# 1213 output tokens for two answers) and fail at 8192 with HTTP 500 after ~110 s
# — the gateway's own timeout, not ours. 4096 leaves headroom for three answers;
# a response that still stops at the limit is reported as `truncated`, never
# parsed as a broken report.
DEFAULT_MAX_TOKENS = 4096
ENV_MAX_TOKENS = 'ANSWER_COMPARE_SONNET_MAX_TOKENS'

STOP_REASON_MAX_TOKENS = 'max_tokens'


def max_tokens() -> int:
    """The configured output limit, read from the environment at call time."""
    raw = (os.environ.get(ENV_MAX_TOKENS) or '').strip()
    if not raw:
        return DEFAULT_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        log.warning('%s is not an integer, using the default max_tokens', ENV_MAX_TOKENS)
        return DEFAULT_MAX_TOKENS
    if value <= 0:
        log.warning('%s must be positive, using the default max_tokens', ENV_MAX_TOKENS)
        return DEFAULT_MAX_TOKENS
    return value


def split_system(messages: list[dict[str, str]]) -> tuple[Optional[str], list[dict[str, str]]]:
    """Anthropic takes the system prompt as a top-level field, not a message.

    The judge builds one ``system`` and one ``user`` message; the system text
    moves to ``system`` and the rest go through unchanged.
    """
    system_parts = [m['content'] for m in messages if m.get('role') == 'system']
    rest = [m for m in messages if m.get('role') != 'system']
    return ('\n\n'.join(system_parts) if system_parts else None), rest


def _first_text_block(body: dict[str, Any]) -> str:
    """The text of the first content block whose ``type`` is ``text``.

    Not ``content[0]``: with extended thinking or structured output the first
    block can be another type, and the text sits after it.
    """
    content = body.get('content')
    if not isinstance(content, list) or not content:
        raise MalformedResponseError('The provider returned no content blocks.')

    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text':
            text = block.get('text')
            if isinstance(text, str) and text.strip():
                return text
            raise MalformedResponseError('The provider returned an empty text block.')

    raise MalformedResponseError('The provider returned no text block.')


def _anthropic_error(body: dict[str, Any]) -> tuple[str, str]:
    """(error type, human-readable message) from an Anthropic-shaped error body.

    ``{"type": "error", "error": {"type": "...", "message": "..."}}``; anything
    else yields empty strings so the status code decides on its own.
    """
    error = body.get('error')
    if not isinstance(error, dict):
        return '', ''
    kind = error.get('type')
    message = error.get('message')
    return (kind if isinstance(kind, str) else ''), (message if isinstance(message, str) else '')


def _raise_for_status(response: httpx.Response, api_key: str) -> None:
    """Translate an HTTP failure into the shared typed errors. Never echoes the body."""
    status = response.status_code
    if status < 400:
        return

    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    kind, message = _anthropic_error(body)
    upstream = scrub_secret(message or (response.text or ''), api_key)[:UPSTREAM_MESSAGE_MAX_CHARS]

    if status in (401, 403) or kind in ('authentication_error', 'permission_error'):
        raise AuthError(
            f'The provider rejected our credentials (HTTP {status}).', status=status, upstream_message=upstream
        )
    if status == 429 or kind == 'rate_limit_error':
        raise RateLimitError(
            'The provider rate-limited the request (HTTP 429).', status=status, upstream_message=upstream
        )
    if status in (400, 413, 422) and kind == 'invalid_request_error' and _mentions_context(message):
        raise ContextLengthExceededError(
            "The input exceeded the provider's context window.", status=status, upstream_message=upstream
        )
    raise UpstreamError(f'The provider returned HTTP {status}.', status=status, upstream_message=upstream)


def _mentions_context(message: str) -> bool:
    lowered = (message or '').lower()
    return 'context' in lowered or 'too many tokens' in lowered or 'prompt is too long' in lowered


async def messages_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    extra_body: Optional[dict[str, Any]] = None,
    extra_headers: Optional[dict[str, str]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> CompletionResult:
    """One Messages-API completion. Raises a ProviderCallError subclass on failure.

    ``extra_body`` is merged into the request body and returned as ``params``,
    together with ``max_tokens``, any beta header sent, and the output token
    count — the record of what was sent and what it cost.
    """
    url = f'{base_url.rstrip("/")}/v1/messages'
    system, rest = split_system(messages)

    limit = max_tokens()
    params: dict[str, Any] = {'max_tokens': limit, **(extra_body or {})}
    payload: dict[str, Any] = {'model': model, 'messages': rest, **params}
    if system:
        payload['system'] = system

    headers = {
        'Authorization': f'Bearer {api_key}',
        'anthropic-version': ANTHROPIC_VERSION,
        'Content-Type': 'application/json',
        **(extra_headers or {}),
    }
    if extra_headers:
        # Recorded so the diagnostics show whether a beta feature was requested.
        params['headers_sent'] = sorted(extra_headers)

    timeout = httpx.Timeout(request_timeout_seconds(), connect=DEFAULT_CONNECT_TIMEOUT_SECONDS)

    owns_client = client is None
    # Looked up on the module at call time, not imported by name: the tests
    # install their transport double over that one seam, for both formats.
    http = client or openai_client._build_async_client(timeout)
    try:
        try:
            response = await http.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise TimeoutError_('The provider did not respond in time.') from None
        except httpx.HTTPError as err:
            log.warning('answer-compare anthropic request failed: %s', type(err).__name__)
            raise NetworkError('Could not reach the provider.') from None

        _raise_for_status(response, api_key)

        try:
            body = response.json()
        except ValueError:
            raise MalformedResponseError('The provider returned a body that is not JSON.') from None
        if not isinstance(body, dict):
            raise MalformedResponseError('The provider returned a body that is not an object.')

        usage = body.get('usage')
        if isinstance(usage, dict) and isinstance(usage.get('output_tokens'), int):
            params['output_tokens'] = usage['output_tokens']

        # Read before anything is parsed: a report cut off at the limit is a
        # truncation, not a malformed report.
        if body.get('stop_reason') == STOP_REASON_MAX_TOKENS:
            raise TruncatedError(
                f'The provider stopped at its output limit ({limit} tokens) before finishing.',
                params=params,
            )

        reported_model = body.get('model')
        return CompletionResult(
            content=_first_text_block(body),
            model=reported_model if isinstance(reported_model, str) and reported_model.strip() else None,
            engine_version=None,
            params=params,
        )
    finally:
        if owns_client:
            await http.aclose()
