"""The adjudicator's native transport, and where its key comes from.

Nothing here touches the network. The SDK client is replaced by a double, and
Key Vault by a stub coroutine — which is the point: the behaviour that matters
(what shape is sent, which failure becomes which code, that the key never
appears in anything stored or logged) is all decidable without spending money or
needing credentials.

The live suite in ``test_live_provider.py`` covers the other half — that a real
model returns something the rubric accepts — and skips when unconfigured.
"""

import asyncio
import logging
import pathlib
from typing import Any, Optional

import anthropic
import pytest

from open_webui.utils import answer_compare_adjudication as adjudication
from open_webui.utils import answer_compare_anthropic as native
from open_webui.utils import answer_compare_client as client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_secrets as secrets

SENTINEL_KEY = 'sk-ant-api03-SENTINEL-do-not-leak'

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


####################
# Doubles
####################


class _Block:
    def __init__(self, type_: str, text: str = '') -> None:
        self.type = type_
        self.text = text


class _Message:
    def __init__(
        self,
        content: list[_Block],
        stop_reason: str = 'end_turn',
        model: str = 'claude-sonnet-5',
        stop_details: Any = None,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.stop_details = stop_details


class _Stream:
    def __init__(self, message: Any, raises: Optional[Exception]) -> None:
        self._message = message
        self._raises = raises

    async def __aenter__(self):
        if self._raises is not None:
            raise self._raises
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self._message


class _Messages:
    def __init__(self, parent: '_SdkDouble') -> None:
        self._parent = parent

    def stream(self, **kwargs):
        self._parent.calls.append(kwargs)
        return _Stream(self._parent.message, self._parent.raises)


class _SdkDouble:
    """Records every request; replies with one message, or raises."""

    def __init__(self, message: Any = None, raises: Optional[Exception] = None) -> None:
        self.message = message if message is not None else _Message([_Block('text', '{"ok": true}')])
        self.raises = raises
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.messages = _Messages(self)

    async def close(self) -> None:
        self.closed = True


def _api_error(cls: type, status: int, body: str = 'boom') -> Exception:
    """An SDK error of the right class without a real HTTP round trip."""
    import httpx

    request = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    response = httpx.Response(status, request=request, text=body)
    return cls(message=body, response=response, body=None)


def _run(coro):
    return asyncio.run(coro)


def _adjudicate(double: _SdkDouble, schema: Optional[dict] = None, messages=None):
    return _run(
        native.adjudicate(
            base_url='https://api.anthropic.com',
            api_key=SENTINEL_KEY,
            model='claude-sonnet-5',
            messages=messages or [{'role': 'system', 'content': 'RULES'}, {'role': 'user', 'content': 'JUDGE'}],
            schema=schema if schema is not None else {'type': 'object'},
            sdk_client=double,
        )
    )


####################
# The request that goes out
####################


def test_the_system_prompt_is_a_top_level_parameter_not_a_message():
    """The adjudication contract must carry system authority, not user authority.

    Left in ``messages``, the rubric would read as something a user asked for —
    the same standing as the candidate text it is supposed to judge, which is the
    authority boundary the injection rules rest on.
    """
    double = _SdkDouble()
    _adjudicate(double)

    sent = double.calls[0]
    assert sent['system'] == 'RULES'
    assert [m['role'] for m in sent['messages']] == ['user']
    assert all(m['role'] != 'system' for m in sent['messages'])


def test_the_rubric_schema_is_enforced_by_the_api():
    schema = adjudication.report_schema(['A', 'B'])
    double = _SdkDouble()
    _adjudicate(double, schema=schema)

    output_config = double.calls[0]['output_config']
    assert output_config['format'] == {'type': 'json_schema', 'schema': schema}
    assert output_config['effort'] == native.DEFAULT_EFFORT


def test_no_sampling_parameters_are_ever_sent():
    """Current models reject them with a 400, and a judge should not be sampling."""
    double = _SdkDouble()
    _adjudicate(double)

    sent = double.calls[0]
    for forbidden in ('temperature', 'top_p', 'top_k'):
        assert forbidden not in sent, forbidden


def test_thinking_is_adaptive_and_never_returned():
    """Depth is the model's to choose; the reasoning text is never requested.

    The adjudication row is an audit record, and chain-of-thought must not be in it.
    """
    double = _SdkDouble()
    _adjudicate(double)

    assert double.calls[0]['thinking'] == {'type': 'adaptive', 'display': 'omitted'}
    assert native.THINKING_DISPLAY == 'omitted'


def test_the_response_is_streamed():
    """A full three-candidate report is long enough that a non-streaming request
    risks an HTTP timeout instead of an answer."""
    double = _SdkDouble()
    result = _adjudicate(double)
    assert result.params['stream'] is True


def test_a_request_with_no_user_message_is_refused_before_it_is_sent():
    double = _SdkDouble()
    with pytest.raises(client.MalformedResponseError):
        _adjudicate(double, messages=[{'role': 'system', 'content': 'RULES'}])
    assert double.calls == []


####################
# The audit record
####################


def test_the_audit_params_name_the_shape_and_carry_no_secret_or_prompt():
    double = _SdkDouble()
    schema = adjudication.report_schema(['A', 'B', 'C'])
    result = _adjudicate(double, schema=schema)

    serialized = str(result.params)
    assert SENTINEL_KEY not in serialized
    assert 'RULES' not in serialized and 'JUDGE' not in serialized
    # The shape is named; the schema itself is not repeated, since the rubric
    # version on the same row already identifies it.
    assert result.params['output_config']['format'] == {'type': 'json_schema'}
    assert 'schema' not in result.params['output_config']['format']


def test_the_adjudicator_records_no_ladder_because_it_walks_none():
    called = _run(
        judge.call_judge(
            provider_id='anthropic',
            base_url='https://api.anthropic.com',
            api_key=SENTINEL_KEY,
            model='claude-sonnet-5',
            messages=[{'role': 'system', 'content': 'R'}, {'role': 'user', 'content': 'U'}],
            labels=['A', 'B'],
            adjudication_call=lambda **kw: _as_coro(
                client.CompletionResult(content='{}', model='claude-sonnet-5', params={'stream': True})
            ),
        )
    )
    assert called.params['structured_output_mode'] == judge.MODE_NATIVE_JSON_SCHEMA
    assert called.params['structured_output_rejections'] == []
    # Distinguishable from "step 1 of the ladder worked", which is mode 1.
    assert judge.MODE_NATIVE_JSON_SCHEMA != judge.MODE_JSON_SCHEMA


async def _as_coro(value):
    return value


def test_a_candidate_never_reaches_the_native_transport():
    """The dispatch is on the judge set, so a generator keeps the ladder."""
    marker: list[str] = []

    async def ladder(base_url, api_key, model, messages, extra_body=None):
        marker.append('ladder')
        return client.CompletionResult(content='{}', model=model, params={'stream': False})

    async def never(**kwargs):
        raise AssertionError('a candidate must not use the adjudicator transport')

    _run(
        judge.call_judge(
            provider_id='chatgpt',
            base_url='https://x/v1',
            api_key='k',
            model='m',
            messages=[{'role': 'user', 'content': 'U'}],
            labels=['A', 'B'],
            completion=ladder,
            adjudication_call=never,
        )
    )
    assert marker == ['ladder']


####################
# Failures, as the one error vocabulary the page already knows
####################


@pytest.mark.parametrize(
    ('sdk_error', 'expected_code'),
    [
        (anthropic.APITimeoutError(request=None), 'timeout'),
        (_api_error(anthropic.AuthenticationError, 401), 'auth'),
        (_api_error(anthropic.PermissionDeniedError, 403), 'auth'),
        (_api_error(anthropic.RateLimitError, 429), 'rate_limit'),
        (_api_error(anthropic.InternalServerError, 500), 'upstream_error'),
    ],
)
def test_sdk_errors_become_the_shared_error_codes(sdk_error, expected_code):
    double = _SdkDouble(raises=sdk_error)
    with pytest.raises(client.ProviderCallError) as caught:
        _adjudicate(double)
    assert caught.value.code == expected_code


def test_an_over_long_prompt_is_its_own_code_not_a_generic_bad_request():
    """The one 400 a shorter input would fix must say so."""
    double = _SdkDouble(raises=_api_error(anthropic.BadRequestError, 400, 'prompt is too long: 1200000 tokens'))
    with pytest.raises(client.ContextLengthExceededError) as caught:
        _adjudicate(double)
    assert caught.value.code == 'context_length_exceeded'


def test_an_unrelated_bad_request_stays_a_bad_request():
    double = _SdkDouble(raises=_api_error(anthropic.BadRequestError, 400, 'unknown field wibble'))
    with pytest.raises(client.ProviderCallError) as caught:
        _adjudicate(double)
    assert caught.value.code == 'upstream_error'
    assert caught.value.status == 400


def test_a_truncated_report_is_malformed_not_silently_scored():
    """`max_tokens` produces genuine JSON up to the cut, which would otherwise
    fail validation with a confusing message about a missing field."""
    double = _SdkDouble(_Message([_Block('text', '{"candidates": [')], stop_reason='max_tokens'))
    with pytest.raises(client.MalformedResponseError) as caught:
        _adjudicate(double)
    assert native.ENV_MAX_TOKENS in caught.value.message


def test_a_refusal_is_reported_as_one():
    class _Details:
        category = 'cyber'

    double = _SdkDouble(_Message([], stop_reason='refusal', stop_details=_Details()))
    with pytest.raises(client.ProviderCallError) as caught:
        _adjudicate(double)
    assert 'declined' in caught.value.message
    assert 'cyber' in caught.value.message


def test_an_empty_response_is_malformed():
    double = _SdkDouble(_Message([_Block('text', '   ')]))
    with pytest.raises(client.MalformedResponseError):
        _adjudicate(double)


def test_thinking_blocks_never_reach_the_stored_content():
    """Even if a deployment turned display on, reasoning must not be stored."""
    double = _SdkDouble(_Message([_Block('thinking', 'my private reasoning'), _Block('text', '{"ok": true}')]))
    result = _adjudicate(double)
    assert result.content == '{"ok": true}'
    assert 'private reasoning' not in result.content


@pytest.mark.parametrize(
    'sdk_error',
    [
        _api_error(anthropic.AuthenticationError, 401, f'x-api-key: {SENTINEL_KEY} rejected'),
        _api_error(anthropic.BadRequestError, 400, f'echoing Bearer {SENTINEL_KEY} back at you'),
        _api_error(anthropic.InternalServerError, 500, f'trace included {SENTINEL_KEY}'),
    ],
)
def test_no_error_path_ever_carries_the_key(sdk_error, caplog):
    """An API error can echo the request back, and these strings are stored and logged."""
    double = _SdkDouble(raises=sdk_error)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(client.ProviderCallError) as caught:
            _adjudicate(double)

    err = caught.value
    assert SENTINEL_KEY not in err.message
    assert SENTINEL_KEY not in (err.upstream_message or '')
    assert SENTINEL_KEY not in caplog.text


def test_an_injected_client_is_not_closed_out_from_under_its_owner():
    double = _SdkDouble(raises=_api_error(anthropic.InternalServerError, 500))
    with pytest.raises(client.ProviderCallError):
        _adjudicate(double)
    assert double.closed is False


@pytest.mark.parametrize('failing', [False, True])
def test_a_client_this_transport_built_is_always_closed(monkeypatch, failing):
    """A leaked client per adjudication is a leaked connection pool.

    Parameterised over success and failure because the failure path is the one
    that leaks if the close is not in a ``finally``.
    """
    double = _SdkDouble(raises=_api_error(anthropic.InternalServerError, 500) if failing else None)
    monkeypatch.setattr(native, '_build_client', lambda api_key, base_url, timeout: double)

    call = native.adjudicate(
        base_url='https://api.anthropic.com',
        api_key=SENTINEL_KEY,
        model='claude-sonnet-5',
        messages=[{'role': 'system', 'content': 'R'}, {'role': 'user', 'content': 'U'}],
        schema={'type': 'object'},
    )
    if failing:
        with pytest.raises(client.ProviderCallError):
            _run(call)
    else:
        _run(call)
    assert double.closed is True


####################
# Configuration guards
####################


def test_effort_falls_back_on_a_level_this_sdk_does_not_accept(monkeypatch, caplog):
    """`xhigh` exists in the API but not in the pinned SDK's literal — sending it
    would be a 400 on every adjudication, so it must not reach the request."""
    monkeypatch.setenv(native.ENV_EFFORT, 'xhigh')
    with caplog.at_level('WARNING', logger=native.__name__):
        assert native.effort() == native.DEFAULT_EFFORT
    assert native.ENV_EFFORT in caplog.text


@pytest.mark.parametrize('level', list(native.EFFORT_LEVELS))
def test_every_advertised_effort_level_is_accepted(monkeypatch, level):
    monkeypatch.setenv(native.ENV_EFFORT, level)
    assert native.effort() == level


@pytest.mark.parametrize('bad', ['0', '-1', 'lots'])
def test_a_bad_max_tokens_falls_back_rather_than_raising(monkeypatch, bad):
    monkeypatch.setenv(native.ENV_MAX_TOKENS, bad)
    assert native.max_tokens() == native.DEFAULT_MAX_TOKENS


####################
# Where the key comes from
####################


@pytest.fixture(autouse=True)
def clean_secret_state(monkeypatch):
    secrets.clear_cache()
    for name in (
        'ANSWER_COMPARE_ANTHROPIC_API_KEY',
        secrets.key_vault_url_env('anthropic'),
        secrets.key_vault_secret_env('anthropic'),
        secrets.ENV_CACHE_TTL_SECONDS,
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    secrets.clear_cache()


def test_no_source_configured_resolves_to_empty_not_an_error():
    """ "Nobody set this up" and "the vault is down" must stay different things."""
    assert _run(secrets.resolve_key('anthropic', '')) == ''
    assert secrets.has_key_source('anthropic', '') is False


def test_the_direct_variable_wins_over_the_vault(monkeypatch):
    """Break-glass: a way back to a working page on the day the vault is broken."""
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')

    def explode(source):
        raise AssertionError('the vault must not be consulted when a direct key is set')

    monkeypatch.setattr(secrets, '_fetch_from_vault', explode)
    assert _run(secrets.resolve_key('anthropic', SENTINEL_KEY)) == SENTINEL_KEY


def test_a_half_configured_vault_is_not_a_source(monkeypatch):
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    assert secrets.key_vault_source('anthropic') is None
    assert secrets.has_key_source('anthropic', '') is False


def test_the_vault_is_read_once_and_then_cached(monkeypatch):
    """Key Vault is rate-limited and an adjudication is not a rare event."""
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')
    reads: list[str] = []

    async def fetch(source):
        reads.append(source.secret_name)
        return SENTINEL_KEY

    monkeypatch.setattr(secrets, '_fetch_from_vault', fetch)

    async def three_times():
        return [await secrets.resolve_key('anthropic', '') for _ in range(3)]

    assert _run(three_times()) == [SENTINEL_KEY] * 3
    assert reads == ['claude-key']


def test_a_zero_ttl_disables_the_cache(monkeypatch):
    """So a deployment that must pick up a rotation immediately can."""
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')
    monkeypatch.setenv(secrets.ENV_CACHE_TTL_SECONDS, '0')
    reads: list[str] = []

    async def fetch(source):
        reads.append(source.secret_name)
        return SENTINEL_KEY

    monkeypatch.setattr(secrets, '_fetch_from_vault', fetch)

    async def twice():
        await secrets.resolve_key('anthropic', '')
        await secrets.resolve_key('anthropic', '')

    _run(twice())
    assert reads == ['claude-key', 'claude-key']


def test_concurrent_adjudications_make_one_vault_call(monkeypatch):
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')
    reads: list[str] = []

    async def fetch(source):
        reads.append(source.secret_name)
        await asyncio.sleep(0)
        return SENTINEL_KEY

    monkeypatch.setattr(secrets, '_fetch_from_vault', fetch)

    async def together():
        return await asyncio.gather(*(secrets.resolve_key('anthropic', '') for _ in range(5)))

    assert _run(together()) == [SENTINEL_KEY] * 5
    assert reads == ['claude-key']


def test_a_vault_failure_is_typed_and_names_no_secret_value(monkeypatch):
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')

    async def fetch(source):
        raise secrets.SecretResolutionError(
            f'Secret {source.secret_name!r} was not found in Key Vault {source.vault_url}.',
            code='secret_not_found',
        )

    monkeypatch.setattr(secrets, '_fetch_from_vault', fetch)

    with pytest.raises(secrets.SecretResolutionError) as caught:
        _run(secrets.resolve_key('anthropic', ''))

    assert caught.value.code == 'secret_not_found'
    assert SENTINEL_KEY not in caught.value.message
    # The location is nameable; the value never is.
    assert 'claude-key' in caught.value.message


def test_a_failed_read_is_not_cached(monkeypatch):
    """Otherwise a transient vault blip would keep the page broken for a whole TTL."""
    monkeypatch.setenv(secrets.key_vault_url_env('anthropic'), 'https://v.vault.azure.net')
    monkeypatch.setenv(secrets.key_vault_secret_env('anthropic'), 'claude-key')
    attempts: list[int] = []

    async def fetch(source):
        attempts.append(1)
        if len(attempts) == 1:
            raise secrets.SecretResolutionError('transient', code='secret_unavailable')
        return SENTINEL_KEY

    monkeypatch.setattr(secrets, '_fetch_from_vault', fetch)

    with pytest.raises(secrets.SecretResolutionError):
        _run(secrets.resolve_key('anthropic', ''))
    assert _run(secrets.resolve_key('anthropic', '')) == SENTINEL_KEY
    assert len(attempts) == 2


@pytest.mark.parametrize(
    'name',
    [
        'judge_api_key_gatekeeper_name',  # underscores — the likeliest real typo
        'has space',
        'dot.name',
        'a' * 128,  # 127 is the limit
        '',
    ],
)
def test_an_invalid_secret_name_fails_before_any_vault_call(monkeypatch, name):
    """Key Vault allows letters, digits and hyphens only.

    Checked locally because the service's rejection is opaque: an invalid name
    comes back as a generic 4xx that reads like a permissions problem, sending
    the reader to the service principal's access policy instead of to the typo.
    """
    source = secrets.KeyVaultSource(vault_url='https://v.vault.azure.net', secret_name=name)
    touched: list[str] = []

    def explode(*args, **kwargs):
        touched.append('network')
        raise AssertionError('an invalid name must not reach the network')

    monkeypatch.setattr('azure.keyvault.secrets.aio.SecretClient', explode, raising=False)

    with pytest.raises(secrets.SecretResolutionError) as caught:
        _run(secrets._fetch_from_vault(source))

    assert caught.value.code == 'secret_name_invalid'
    assert touched == []


@pytest.mark.parametrize('name', ['claude-api-key', 'judge-api-key-gatekeeper-name', 'k', 'a' * 127])
def test_a_valid_secret_name_is_not_rejected_by_the_guard(name):
    """The guard must not be stricter than Key Vault itself."""
    assert secrets.SECRET_NAME_RE.fullmatch(name), name


def test_the_invalid_name_error_has_page_copy():
    """Every code this module can emit must reach the admin as words, not a code."""
    copy = (REPO_ROOT / 'src' / 'lib' / 'components' / 'admin' / 'AnswerCompare' / 'judgeState.ts').read_text()
    emitted = {
        'secret_unavailable',
        'secret_auth',
        'secret_not_found',
        'secret_timeout',
        'secret_empty',
        'secret_backend_missing',
        'secret_name_invalid',
    }
    for code in sorted(emitted):
        assert f'{code}:' in copy, code
