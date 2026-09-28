"""VQ-25: the adjudicator's transport — Anthropic's Messages API, natively.

The three compared candidates are reached through
``answer_compare_client``, one OpenAI-compatible chat-completions call each. The
adjudicator cannot be: ``api.anthropic.com`` serves no OpenAI-shaped
``/v1/chat/completions``, so that client pointed here would fail every request.
This module is the adjudicator's own transport, over the official ``anthropic``
SDK, and it is the only place in the feature that speaks the Messages API.

Going native is not merely a compatibility fix — it removes machinery. The
candidates' path walks a structured-output *ladder* (``json_schema`` →
``json_object`` → plain prose, plus dropping ``temperature``) because a gateway
may reject any of those shapes and the only way to find out is to try. Here
``output_config.format`` is a first-class guarantee of schema-valid JSON, so
there is one shape, one attempt, and no ladder to record. An adjudication that
comes back is either valid against the rubric's schema or a typed failure.

Three deliberate choices:

* **Thinking is adaptive with ``display: "omitted"``.** Depth is the model's to
  choose; the reasoning text is never requested, because the adjudication record
  is auditable and must never carry chain-of-thought. The visible output is the
  findings and the category points — the things the server re-computes from.
* **Streaming, always.** A full adjudication of three candidates across ten
  weighted categories, with per-claim classifications, is a long response;
  a non-streaming request of that size risks an HTTP timeout rather than an
  answer.
* **Sampling parameters are never sent.** Current Claude models reject
  ``temperature``/``top_p``/``top_k`` with a 400, and an adjudicator should be as
  close to deterministic as the API allows anyway.

Failures are mapped onto the same ``ProviderCallError`` subclasses the
OpenAI-compatible client raises, so the router, the error copy and the page
branch on one vocabulary regardless of which transport ran. Key material never
leaves this module: it goes into the SDK client and is scrubbed out of every
message kept for diagnostics, because an API error can echo the request back.
"""

import logging
import os
from typing import Any, Optional

from open_webui.utils import answer_compare_client as client

log = logging.getLogger(__name__)

# Reasoning at depth is slow, and this one request carries the whole
# adjudication, so the read budget is generous. It must stay below any proxy
# timeout in front of this server, exactly as the other transport's does.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0
ENV_REQUEST_TIMEOUT_SECONDS = 'ANSWER_COMPARE_ANTHROPIC_TIMEOUT_SECONDS'

# Room for the full report. A truncated response is not a partial adjudication —
# it is invalid JSON, and is reported as malformed rather than scored.
DEFAULT_MAX_TOKENS = 32_000
ENV_MAX_TOKENS = 'ANSWER_COMPARE_ANTHROPIC_MAX_TOKENS'

# Adjudication is the intelligence-sensitive path in this feature, so it does not
# run at the cheap end. Overridable per deployment; the SDK pinned here accepts
# low / medium / high / max.
DEFAULT_EFFORT = 'high'
ENV_EFFORT = 'ANSWER_COMPARE_ANTHROPIC_EFFORT'
EFFORT_LEVELS: tuple[str, ...] = ('low', 'medium', 'high', 'max')

# What the API calls the reasoning it does not show us. Recorded in the audit
# params so a later reader knows depth was requested and text was not.
THINKING_DISPLAY = 'omitted'


def request_timeout_seconds() -> float:
    return _positive_float_env(ENV_REQUEST_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS)


def max_tokens() -> int:
    return int(_positive_float_env(ENV_MAX_TOKENS, float(DEFAULT_MAX_TOKENS)))


def effort() -> str:
    raw = (os.environ.get(ENV_EFFORT) or '').strip().lower()
    if not raw:
        return DEFAULT_EFFORT
    if raw in EFFORT_LEVELS:
        return raw
    log.warning(
        '%s=%r is not one of %s — using %s',
        ENV_EFFORT,
        raw,
        ', '.join(EFFORT_LEVELS),
        DEFAULT_EFFORT,
    )
    return DEFAULT_EFFORT


def _positive_float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning('%s is not a number, using the default', name)
        return default
    if value <= 0:
        log.warning('%s must be positive, using the default', name)
        return default
    return value


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Lift the system message out of an OpenAI-shaped message list.

    The Messages API takes the system prompt as a **top-level** ``system``
    parameter, not as a ``messages`` entry. Leaving it in the list would either be
    rejected or — worse — silently turn the whole adjudication contract into
    something the model reads as a user's request, which is precisely the
    authority boundary the rubric's injection rules depend on.

    Several system messages are joined in order, so the caller is free to build
    the contract in pieces.
    """
    system_parts = [m.get('content', '') for m in messages if m.get('role') == 'system']
    rest = [m for m in messages if m.get('role') != 'system']
    return '\n\n'.join(part for part in system_parts if part), rest


def _build_client(api_key: str, base_url: str, timeout: float):
    """The SDK client. Its own function so tests can replace the whole transport.

    Imported lazily so this module can be imported — and its pure helpers tested —
    without the SDK installed, and so a missing package surfaces here rather than
    at application start.
    """
    import anthropic

    kwargs: dict[str, Any] = {'api_key': api_key, 'timeout': timeout, 'max_retries': 0}
    if base_url:
        kwargs['base_url'] = base_url
    return anthropic.AsyncAnthropic(**kwargs)


def _text_of(message: Any) -> str:
    """The response's text blocks, joined.

    Thinking blocks are skipped rather than concatenated: with
    ``display: "omitted"`` they carry no text, and if a deployment ever turns
    display on they must still never reach the stored adjudication.
    """
    parts = []
    for block in getattr(message, 'content', None) or []:
        if getattr(block, 'type', None) == 'text':
            parts.append(getattr(block, 'text', '') or '')
    return ''.join(parts)


def _raise_for_stop_reason(message: Any, api_key: str) -> None:
    """Turn a non-answer into a typed failure instead of unparseable content.

    ``max_tokens`` is the one that would otherwise be subtle: the response is
    genuine JSON right up to where it was cut, so it fails schema validation with
    a confusing message about a missing field rather than "the report did not
    fit". ``refusal`` is a policy decline, which is a real outcome for a model
    asked to judge text it did not write.
    """
    stop_reason = getattr(message, 'stop_reason', None)
    if stop_reason == 'max_tokens':
        raise client.MalformedResponseError(
            'The adjudication was cut off before it finished. '
            f'Raise {ENV_MAX_TOKENS} (currently {max_tokens()}) or reduce the input.',
            code='malformed_response',
        )
    if stop_reason == 'refusal':
        details = getattr(message, 'stop_details', None)
        category = getattr(details, 'category', None) or 'unspecified'
        raise client.UpstreamError(
            f'The adjudicator declined to answer (category: {category}).',
            code='upstream_error',
            upstream_message=client.scrub_secret(str(category), api_key),
        )


def _mapped_error(err: Exception, api_key: str) -> client.ProviderCallError:
    """One SDK exception, as the error vocabulary the rest of the feature uses.

    Every message is scrubbed: an API error can echo the request — headers
    included — and these strings are stored on the report row and logged.
    """
    import anthropic

    excerpt = client.scrub_secret(str(err), api_key)[: client.UPSTREAM_MESSAGE_MAX_CHARS]
    status = getattr(err, 'status_code', None)

    if isinstance(err, anthropic.APITimeoutError):
        return client.TimeoutError_(
            f'The adjudicator did not respond within {request_timeout_seconds():.0f}s.',
            code='timeout',
        )
    if isinstance(err, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return client.AuthError('The adjudicator rejected the credentials.', status=status)
    if isinstance(err, anthropic.RateLimitError):
        return client.RateLimitError(
            'The adjudicator is rate limiting. Try again shortly.',
            status=status,
            upstream_message=excerpt,
        )
    if isinstance(err, anthropic.BadRequestError):
        # The API reports an over-long prompt as a 400. It is its own code
        # because the page must be able to say exactly that, rather than showing
        # a generic bad-request for the one failure a shorter input would fix.
        if 'too long' in excerpt.lower() or 'context' in excerpt.lower():
            return client.ContextLengthExceededError(
                "The input is longer than the adjudicator's context window.",
                status=status,
                upstream_message=excerpt,
            )
        return client.UpstreamError('The adjudicator rejected the request.', status=status, upstream_message=excerpt)
    if isinstance(err, anthropic.APIConnectionError):
        return client.NetworkError('Could not reach the adjudicator.', code='network')
    if isinstance(err, anthropic.APIStatusError):
        return client.UpstreamError('The adjudicator returned an error.', status=status, upstream_message=excerpt)
    return client.UpstreamError('The adjudicator returned an error.', upstream_message=excerpt)


async def adjudicate(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    sdk_client: Optional[Any] = None,
) -> client.CompletionResult:
    """One adjudication call. Raises a ``ProviderCallError`` subclass on failure.

    ``schema`` is the rubric's own report schema, enforced by the API rather than
    hoped for in the prompt. ``sdk_client`` exists so tests can inject a double;
    the application always lets this build its own.

    The returned ``params`` is the record of what was sent — it rides into the
    stored audit row, so it names the shape, the effort and the thinking mode,
    and contains no key, no base URL and no prompt.
    """
    import anthropic

    system, conversation = split_system(messages)
    if not conversation:
        raise client.MalformedResponseError('The adjudication request has no user message.')

    timeout = request_timeout_seconds()
    limit = max_tokens()
    depth = effort()
    thinking = {'type': 'adaptive', 'display': THINKING_DISPLAY}

    # The audit record names the shape without carrying the schema itself: the
    # schema is versioned by the engine's own rubric version, which is already
    # on the row, so repeating hundreds of lines of it per adjudication would
    # bloat every record to say something already said.
    params: dict[str, Any] = {
        'stream': True,
        'max_tokens': limit,
        'output_config': {'effort': depth, 'format': {'type': 'json_schema'}},
        'thinking': dict(thinking),
    }

    owns_client = sdk_client is None
    api = sdk_client or _build_client(api_key, base_url, timeout)
    try:
        async with api.messages.stream(
            model=model,
            max_tokens=limit,
            system=system,
            messages=conversation,
            thinking=thinking,
            output_config={'effort': depth, 'format': {'type': 'json_schema', 'schema': schema}},
        ) as stream:
            message = await stream.get_final_message()
    except client.ProviderCallError:
        raise
    except anthropic.AnthropicError as err:
        raise _mapped_error(err, api_key) from None
    except Exception as err:  # noqa: BLE001 - anything else is still not an answer
        log.exception('adjudicator transport failed unexpectedly')
        raise _mapped_error(err, api_key) from None
    finally:
        if owns_client:
            await api.close()

    _raise_for_stop_reason(message, api_key)

    content = _text_of(message)
    if not content.strip():
        raise client.MalformedResponseError('The adjudicator returned an empty response.')

    return client.CompletionResult(
        content=content,
        model=getattr(message, 'model', None) or None,
        # There is no engine-version header to read here: the model id the API
        # reports back *is* the build, and inventing one would be a fabricated
        # audit field.
        engine_version=None,
        params=params,
    )
