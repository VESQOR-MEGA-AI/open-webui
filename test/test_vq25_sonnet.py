"""VQ-25: the independent judge — Claude Sonnet over the Anthropic Messages format.

Run from the repository root:  pytest test/test_vq25_sonnet.py -q

DECISIONS.md#016 (owner, 2026-09-15): the three compared providers no longer
judge; one independent judge does. This file covers the DEFAULT panel — the
other VQ-25 suites pin the pre-#016 participant panel through the supported
``ANSWER_COMPARE_<ID>_CAN_JUDGE`` override and are not repeated here.

Same machinery as the other suites: no ``pytest-asyncio``, async driven with
``asyncio.run(...)``, the provider double an ``httpx.MockTransport`` over the
client's ``_build_async_client`` seam. The rule of the file: sonnet is a JUDGE
and never an ANSWER — every test that could confuse the two asserts both halves.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.answer_compare import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    AnswerCompareAnswer,
    AnswerCompareAnswers,
    AnswerCompareReport,
    AnswerCompareReports,
    AnswerCompareRun,
    AnswerCompareRunForm,
    AnswerCompareRuns,
    AnswerCompareSummaries,
    AnswerCompareSummary,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_anthropic_client as anthropic_client
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils import answer_compare_summary as summary
from open_webui.utils import answer_compare_tally as tally
from open_webui.utils.auth import get_current_user

REPO_ROOT = Path(__file__).resolve().parent.parent

DUMMY_KEY = 'sk-ant-vq25-sonnet-secret-do-not-leak'
SONNET_HOST = 'https://sonnet.example/claude'
SONNET_MODEL = 'claude-sonnet-test-1'
PROMPT = 'Compare the answers on latency.'
REFERENCE = 'Latency is per-request; throughput is per-unit-time.'
ANSWER_TEXT = {
    'chatgpt': 'Latency matters most for interactive use. Throughput is a batch concern.',
    'gemini': 'Throughput and latency trade off; caching helps both in practice.',
    'vesqor': 'Measure p99 latency first, then raise throughput without regressing it.',
}
ANSWER_MODEL = {'chatgpt': 'gpt-test-1', 'gemini': 'gemini-test-1', 'vesqor': 'vesqor-reasoning'}

SONNET_ENV = (
    'ANSWER_COMPARE_SONNET_BASE_URL',
    'ANSWER_COMPARE_SONNET_API_KEY',
    'ANSWER_COMPARE_SONNET_MODEL',
)
CAN_JUDGE_ENV = tuple(f'ANSWER_COMPARE_{judge_id.upper()}_CAN_JUDGE' for judge_id in providers.JUDGE_CANDIDATE_IDS)
ANSWER_BLOCK_RE = re.compile(r'=== ANSWER ([A-C]) ===\n(.*?)\n=== END ANSWER \1 ===', re.S)

LABELS = ['A', 'B', 'C']
MESSAGES = [
    {'role': 'system', 'content': 'You are the judge.'},
    {'role': 'user', 'content': 'Judge these.'},
]


####################
# Fixtures and doubles
####################


@pytest.fixture(autouse=True)
def default_panel(monkeypatch):
    """The built-in state: no CAN_JUDGE override anywhere, no sonnet triple, mode cache empty."""
    for name in CAN_JUDGE_ENV + SONNET_ENV:
        monkeypatch.delenv(name, raising=False)
    for judge_id in providers.JUDGE_CANDIDATE_IDS:
        monkeypatch.delenv(providers.judge_max_input_chars_env(judge_id), raising=False)
    monkeypatch.delenv('ANSWER_COMPARE_SONNET_MAX_INPUT_CHARS', raising=False)
    monkeypatch.delenv(provider_client.ENV_REQUEST_TIMEOUT_SECONDS, raising=False)
    monkeypatch.delenv(anthropic_client.ENV_MAX_TOKENS, raising=False)
    monkeypatch.delenv(judge.ENV_SONNET_STRUCTURED_OUTPUT, raising=False)
    judge.reset_mode_cache()
    return monkeypatch


@pytest.fixture
def structured_output_on(default_panel):
    """The schema step, switched on for a real Anthropic endpoint (off on Kie by default)."""
    default_panel.setenv(judge.ENV_SONNET_STRUCTURED_OUTPUT, 'true')
    return default_panel


def configure_sonnet(monkeypatch) -> None:
    monkeypatch.setenv('ANSWER_COMPARE_SONNET_BASE_URL', SONNET_HOST)
    monkeypatch.setenv('ANSWER_COMPARE_SONNET_API_KEY', DUMMY_KEY)
    monkeypatch.setenv('ANSWER_COMPARE_SONNET_MODEL', SONNET_MODEL)


def labels_in(body: dict) -> list[str]:
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    return [m.group(1) for m in ANSWER_BLOCK_RE.finditer(user)]


def valid_report(labels: list[str], winner_index: int = 0, improvements_for: Optional[str] = None) -> dict:
    return {
        'answers': [
            {
                'label': label,
                'strengths': [{'passage': 'quoted bit', 'note': f'strength of {label}'}],
                'errors_or_unsupported': [],
                'omissions': [],
                'useful_extras': [],
                'unnecessary': [],
                'improvements': (
                    [{'passage': 'p99 latency', 'note': 'say which percentile and why'}]
                    if label == improvements_for
                    else []
                ),
            }
            for label in labels
        ],
        'verdict': {'kind': 'winner', 'labels': [labels[winner_index]]},
        'rationale': 'The winner states the trade-off and names a measurement.',
        'needs_verification': ['the p99 figure'],
    }


def messages_response(
    text: Any,
    model: str = 'claude-4.6-sonnet',
    stop_reason: str = 'end_turn',
    output_tokens: int = 321,
    leading_blocks: Optional[list[dict]] = None,
) -> httpx.Response:
    content = text if isinstance(text, str) else json.dumps(text)
    return httpx.Response(
        200,
        json={
            'id': 'msg_test',
            'type': 'message',
            'role': 'assistant',
            'model': model,
            'content': (leading_blocks or []) + [{'type': 'text', 'text': content}],
            'stop_reason': stop_reason,
            'usage': {'input_tokens': 1234, 'output_tokens': output_tokens},
        },
    )


def anthropic_error(status: int, kind: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={'type': 'error', 'error': {'type': kind, 'message': message}})


class SonnetDouble:
    """Records every request; replies from a handler that sees the parsed body."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responder: Callable[[dict, httpx.Request], httpx.Response] = lambda body, request: messages_response(
            valid_report(labels_in(body))
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.requests.append({'url': str(request.url), 'headers': dict(request.headers), 'body': body})
        return self.responder(body, request)

    @property
    def last(self) -> dict:
        return self.requests[-1]['body']

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def install_double(monkeypatch) -> SonnetDouble:
    double = SonnetDouble()

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout)

    monkeypatch.setattr(provider_client, '_build_async_client', build)
    return double


def complete(double: SonnetDouble, **kwargs) -> provider_client.CompletionResult:
    async def _call():
        async with double.client() as http:
            return await anthropic_client.messages_completion(
                SONNET_HOST, DUMMY_KEY, SONNET_MODEL, MESSAGES, client=http, **kwargs
            )

    return asyncio.run(_call())


def _user(role: str) -> UserModel:
    return UserModel(
        id=f'{role}-id',
        email=f'{role}@example.com',
        name=role.capitalize(),
        role=role,
        last_active_at=0,
        updated_at=0,
        created_at=0,
    )


def client_as(role: str = 'admin') -> TestClient:
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    tables = [m.__table__ for m in (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


def make_run() -> str:
    async def _run():
        await _create_tables()
        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email='admin@example.com',
            form=AnswerCompareRunForm(prompt=PROMPT, reference=REFERENCE),
        )
        return run.id

    return asyncio.run(_run())


def seed_answers(run_id: str) -> None:
    async def _seed():
        for provider_id in providers.PROVIDER_IDS:
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run_id,
                provider=provider_id,
                status=STATUS_COMPLETE,
                requested_model=ANSWER_MODEL[provider_id],
                model=f'{ANSWER_MODEL[provider_id]}-resolved',
                text=ANSWER_TEXT[provider_id],
            )

    asyncio.run(_seed())


def stored_reports(run_id: str) -> list:
    return asyncio.run(AnswerCompareReports.get_all_by_run(run_id))


def seed_legacy_report(run_id: str, judge_id: str) -> Any:
    """A complete report written when ``judge_id`` was still a participant judge."""
    versions = asyncio.run(AnswerCompareAnswers.get_current_versions(run_id))
    label_map = {label: v.provider for label, v in zip(LABELS, versions)}

    async def _seed():
        return await AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge=judge_id,
            status=STATUS_COMPLETE,
            requested_model=ANSWER_MODEL[judge_id],
            model=f'{ANSWER_MODEL[judge_id]}-resolved',
            label_map=label_map,
            report=valid_report(LABELS, winner_index=2),
            judged_versions=[{'provider': v.provider, 'revision': v.revision} for v in versions],
            blinding_compromised=[],
        )

    return asyncio.run(_seed())


####################
# 1 — sonnet is a judge, never an answer provider
####################


def test_sonnet_is_in_the_judge_registry_and_not_in_the_answer_registry():
    assert providers.PROVIDER_IDS == ('chatgpt', 'gemini', 'vesqor')
    assert providers.JUDGE_CANDIDATE_IDS == ('chatgpt', 'gemini', 'vesqor', 'sonnet')
    assert providers.api_format('sonnet') == providers.API_FORMAT_ANTHROPIC
    assert [providers.api_format(p) for p in providers.PROVIDER_IDS] == [providers.API_FORMAT_OPENAI] * 3


def test_answers_endpoint_refuses_sonnet_as_an_unknown_provider(default_panel):
    configure_sonnet(default_panel)
    double = install_double(default_panel)
    run_id = make_run()

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/sonnet')

    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'unknown_provider'
    assert double.requests == []


def test_config_lists_sonnet_under_judges_only(default_panel):
    response = client_as().get('/api/v1/compare/config')

    assert response.status_code == 200
    payload = response.json()
    assert [p['id'] for p in payload['providers']] == ['chatgpt', 'gemini', 'vesqor']
    assert [j['id'] for j in payload['judges']] == ['chatgpt', 'gemini', 'vesqor', 'sonnet']
    assert [j['can_judge'] for j in payload['judges']] == [False, False, False, True]
    assert all(p['can_judge'] is False for p in payload['providers'])


def test_env_example_carries_the_judge_variables_and_no_generation_limit_for_sonnet():
    text = (REPO_ROOT / '.env.example').read_text()
    for name in SONNET_ENV + ('ANSWER_COMPARE_SONNET_JUDGE_MAX_INPUT_CHARS',):
        assert name in text, name
    # A generation limit would imply sonnet generates an answer. It does not.
    assert 'ANSWER_COMPARE_SONNET_MAX_INPUT_CHARS' not in text


def test_sonnet_judge_limit_never_reads_a_generation_variable(default_panel):
    default_panel.setenv('ANSWER_COMPARE_SONNET_MAX_INPUT_CHARS', '10')
    assert (
        providers.resolve_judge_max_input_chars('sonnet')
        == providers.DEFAULT_MAX_INPUT_CHARS * providers.JUDGE_MAX_INPUT_CHARS_MULTIPLIER
    )
    default_panel.setenv('ANSWER_COMPARE_SONNET_JUDGE_MAX_INPUT_CHARS', '777')
    assert providers.resolve_judge_max_input_chars('sonnet') == 777


####################
# 2 — sonnet resolves with exactly three variables
####################


def test_sonnet_unconfigured_names_only_its_own_variables(default_panel):
    sonnet = {j.id: j for j in providers.resolve_judges()}['sonnet']
    assert sonnet.configured is False
    assert sonnet.can_judge is True
    assert sonnet.missing == ['ANSWER_COMPARE_SONNET_API_KEY', 'ANSWER_COMPARE_SONNET_MODEL']
    assert sonnet.base_url == 'https://api.kie.ai/claude'
    assert sonnet.model is None


def test_sonnet_configured_by_its_own_triple(default_panel):
    configure_sonnet(default_panel)
    sonnet = {j.id: j for j in providers.resolve_judges()}['sonnet']
    assert sonnet.configured is True
    assert sonnet.missing == []
    assert sonnet.base_url == SONNET_HOST
    assert sonnet.model == SONNET_MODEL
    assert DUMMY_KEY not in json.dumps(sonnet.model_dump())
    assert providers.resolve_api_key('sonnet') == DUMMY_KEY


def test_sonnet_ignores_anthropic_api_key(default_panel):
    default_panel.setenv('ANTHROPIC_API_KEY', 'sk-ant-foreign-do-not-borrow')
    default_panel.setenv('ANSWER_COMPARE_SONNET_MODEL', SONNET_MODEL)
    sonnet = {j.id: j for j in providers.resolve_judges()}['sonnet']
    assert sonnet.configured is False
    assert sonnet.missing == ['ANSWER_COMPARE_SONNET_API_KEY']


def test_default_judge_panel_is_sonnet_alone_and_the_override_re_adds_a_participant(default_panel):
    assert providers.resolve_judge_ids() == ('sonnet',)
    default_panel.setenv('ANSWER_COMPARE_CHATGPT_CAN_JUDGE', 'true')
    assert providers.resolve_judge_ids() == ('chatgpt', 'sonnet')
    default_panel.setenv('ANSWER_COMPARE_SONNET_CAN_JUDGE', 'false')
    assert providers.resolve_judge_ids() == ('chatgpt',)


####################
# 3 — the Anthropic client
####################


def test_request_shape_url_headers_system_and_max_tokens():
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response('hello')

    result = complete(double)

    sent = double.requests[-1]
    assert sent['url'] == f'{SONNET_HOST}/v1/messages'
    assert sent['headers']['authorization'] == f'Bearer {DUMMY_KEY}'
    assert sent['headers']['anthropic-version'] == '2023-06-01'
    assert sent['headers']['content-type'] == 'application/json'
    assert 'x-api-key' not in sent['headers']

    body = sent['body']
    assert body['model'] == SONNET_MODEL
    assert body['system'] == 'You are the judge.'
    assert body['messages'] == [{'role': 'user', 'content': 'Judge these.'}]
    assert body['max_tokens'] == 4096

    assert result.content == 'hello'
    assert result.model == 'claude-4.6-sonnet'
    assert result.engine_version is None
    assert result.params['max_tokens'] == 4096
    assert result.params['output_tokens'] == 321


@pytest.mark.parametrize('raw, expected', [('2048', 2048), (' 8000 ', 8000)])
def test_max_tokens_is_overridable_from_the_environment(default_panel, raw, expected):
    default_panel.setenv(anthropic_client.ENV_MAX_TOKENS, raw)
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response('hello')

    result = complete(double)

    assert double.last['max_tokens'] == expected
    assert result.params['max_tokens'] == expected


@pytest.mark.parametrize('raw', ['lots', '0', '-5', '12.5'])
def test_max_tokens_junk_or_non_positive_falls_back_to_the_default_with_a_warning(default_panel, raw, caplog):
    default_panel.setenv(anthropic_client.ENV_MAX_TOKENS, raw)
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response('hello')

    with caplog.at_level('WARNING', logger=anthropic_client.__name__):
        complete(double)

    assert double.last['max_tokens'] == 4096
    assert anthropic_client.ENV_MAX_TOKENS in caplog.text


def test_the_default_max_tokens_is_the_kie_safe_value():
    # LIVE-CHECK.md: 8192 → HTTP 500 after ~110 s on Kie; 4096 → 200 with headroom.
    assert anthropic_client.DEFAULT_MAX_TOKENS == 4096


def test_extra_body_and_extra_headers_are_sent_and_recorded():
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response('hello')

    result = complete(
        double,
        extra_body={'temperature': 0, 'output_format': {'type': 'json_schema', 'schema': {}}},
        extra_headers={'anthropic-beta': 'structured-outputs-2025-11-13'},
    )

    sent = double.requests[-1]
    assert sent['body']['temperature'] == 0
    assert sent['body']['output_format'] == {'type': 'json_schema', 'schema': {}}
    assert sent['headers']['anthropic-beta'] == 'structured-outputs-2025-11-13'
    assert result.params['temperature'] == 0
    assert result.params['headers_sent'] == ['anthropic-beta']


def test_text_comes_from_the_first_text_block_not_content_zero():
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response(
        'the answer', leading_blocks=[{'type': 'thinking', 'thinking': 'let me think', 'signature': 'x'}]
    )
    assert complete(double).content == 'the answer'


@pytest.mark.parametrize(
    'body',
    [
        {'model': 'm', 'content': []},
        {'model': 'm', 'content': [{'type': 'tool_use', 'id': 't', 'name': 'n', 'input': {}}]},
        {'model': 'm', 'content': [{'type': 'text', 'text': '   '}]},
        {'model': 'm'},
        [],
    ],
)
def test_bodies_without_a_text_block_are_malformed(body):
    double = SonnetDouble()
    double.responder = lambda b, request: httpx.Response(200, json=body)
    with pytest.raises(provider_client.MalformedResponseError):
        complete(double)


def test_non_json_body_is_malformed():
    double = SonnetDouble()
    double.responder = lambda b, request: httpx.Response(200, text='<html>upstream</html>')
    with pytest.raises(provider_client.MalformedResponseError):
        complete(double)


def test_stop_at_max_tokens_is_truncated_not_parsed():
    double = SonnetDouble()
    double.responder = lambda body, request: messages_response('{"answers": [', stop_reason='max_tokens')

    with pytest.raises(provider_client.TruncatedError) as info:
        complete(double)

    assert info.value.code == 'truncated'
    assert '4096' in info.value.message
    assert issubclass(provider_client.TruncatedError, provider_client.ProviderCallError)
    # What the call recorded before the cut-off travels with the error: the
    # failed row must still say how many tokens the limit swallowed.
    assert info.value.params['output_tokens'] == 321
    assert info.value.params['max_tokens'] == 4096


@pytest.mark.parametrize(
    'status, kind, message, expected, expected_code',
    [
        (401, 'authentication_error', 'invalid x-api-key', provider_client.AuthError, 'auth'),
        (403, 'permission_error', 'not allowed', provider_client.AuthError, 'auth'),
        (429, 'rate_limit_error', 'slow down', provider_client.RateLimitError, 'rate_limit'),
        (
            400,
            'invalid_request_error',
            'prompt is too long: 250000 tokens > 200000 maximum',
            provider_client.ContextLengthExceededError,
            'context_length_exceeded',
        ),
        (
            400,
            'invalid_request_error',
            'output_format: Extra inputs are not permitted',
            provider_client.UpstreamError,
            'upstream_error',
        ),
        (500, 'api_error', 'internal', provider_client.UpstreamError, 'upstream_error'),
        (529, 'overloaded_error', 'Overloaded', provider_client.UpstreamError, 'upstream_error'),
    ],
)
def test_anthropic_error_bodies_map_to_the_shared_error_types(status, kind, message, expected, expected_code):
    double = SonnetDouble()
    double.responder = lambda body, request: anthropic_error(status, kind, message)

    with pytest.raises(expected) as info:
        complete(double)

    err = info.value
    assert err.code == expected_code
    assert err.status == status
    # The human-readable Anthropic message is kept as diagnostics, not the raw body.
    assert err.upstream_message == message
    assert '"type"' not in err.upstream_message


def test_a_400_with_a_non_anthropic_body_is_still_a_typed_upstream_error():
    double = SonnetDouble()
    double.responder = lambda body, request: httpx.Response(400, text='Bad Request')

    with pytest.raises(provider_client.UpstreamError) as info:
        complete(double)

    assert info.value.status == 400
    assert info.value.upstream_message == 'Bad Request'


def test_the_key_is_scrubbed_from_an_echoed_error_message():
    double = SonnetDouble()
    double.responder = lambda body, request: anthropic_error(
        400, 'invalid_request_error', f'bad header Authorization: Bearer {DUMMY_KEY} rejected'
    )

    with pytest.raises(provider_client.UpstreamError) as info:
        complete(double)

    assert DUMMY_KEY not in info.value.upstream_message
    assert DUMMY_KEY not in info.value.message
    assert DUMMY_KEY not in str(info.value)


def test_timeout_and_network_failures_are_typed():
    def timeout(body, request):
        raise httpx.ReadTimeout('slow', request=request)

    def network(body, request):
        raise httpx.ConnectError('down', request=request)

    double = SonnetDouble()
    double.responder = timeout
    with pytest.raises(provider_client.TimeoutError_):
        complete(double)

    double.responder = network
    with pytest.raises(provider_client.NetworkError):
        complete(double)


def test_split_system_joins_system_messages_and_keeps_the_rest_in_order():
    system, rest = anthropic_client.split_system(
        [
            {'role': 'system', 'content': 'one'},
            {'role': 'user', 'content': 'u1'},
            {'role': 'system', 'content': 'two'},
            {'role': 'assistant', 'content': 'a1'},
        ]
    )
    assert system == 'one\n\ntwo'
    assert rest == [{'role': 'user', 'content': 'u1'}, {'role': 'assistant', 'content': 'a1'}]
    assert anthropic_client.split_system([{'role': 'user', 'content': 'u'}]) == (
        None,
        [{'role': 'user', 'content': 'u'}],
    )


####################
# 4 — fence-tolerant parsing
####################

FENCED_REPORT = json.dumps(valid_report(LABELS))


@pytest.mark.parametrize(
    'content',
    [
        FENCED_REPORT,
        f'```json\n{FENCED_REPORT}\n```',
        f'```\n{FENCED_REPORT}\n```',
        f'  ```json\n{FENCED_REPORT}\n```  \n',
        f'```JSON\n{FENCED_REPORT}\n```',
    ],
)
def test_parse_report_unwraps_a_code_fence(content):
    assert judge.parse_report(content, LABELS) == valid_report(LABELS)


@pytest.mark.parametrize(
    'content',
    [
        f'Here is the report:\n```json\n{FENCED_REPORT}\n```',
        f'```json\n{FENCED_REPORT}\n```\nLet me know if you need more.',
        f'```json\n{FENCED_REPORT}',
        '```json\n```',
    ],
)
def test_parse_report_does_not_tolerate_prose_around_the_fence(content):
    with pytest.raises(judge.MalformedReport):
        judge.parse_report(content, LABELS)


def test_strip_code_fence_is_the_wrapper_only_never_the_shape():
    assert judge.strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert judge.strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert judge.strip_code_fence('```\n\n{"a": 1}\n\n```') == '{"a": 1}'
    # Not a fence: returned untouched so the JSON parse reports it.
    assert judge.strip_code_fence('```') == '```'
    assert judge.strip_code_fence('``````') == ''


####################
# 5 — the ladder for the Anthropic format
####################


class LadderDouble:
    """A completion double that rejects while ``reject`` says so, recording every attempt."""

    def __init__(self, reject: Callable[[dict, dict], bool], content: Optional[str] = None):
        self.reject = reject
        self.attempts: list[dict] = []
        self.content = content or FENCED_REPORT

    async def __call__(self, base_url, api_key, model, messages, extra_body=None, extra_headers=None):
        attempt = {'body': dict(extra_body or {}), 'headers': dict(extra_headers or {})}
        self.attempts.append(attempt)
        if self.reject(attempt['body'], attempt['headers']):
            raise provider_client.UpstreamError(
                'The provider returned HTTP 400.',
                status=400,
                upstream_message='output_format: Extra inputs are not permitted',
            )
        return provider_client.CompletionResult(
            content=self.content,
            model='claude-4.6-sonnet',
            engine_version=None,
            params={'max_tokens': 4096, **attempt['body']},
        )


def run_ladder(double: LadderDouble, provider_id: str = 'sonnet') -> judge.JudgeCallResult:
    return asyncio.run(
        judge.call_judge(provider_id, SONNET_HOST, DUMMY_KEY, SONNET_MODEL, MESSAGES, LABELS, completion=double)
    )


def test_anthropic_ladder_is_plain_only_by_default():
    """LIVE-CHECK.md: Kie accepts `output_format` and ignores it, so recording the
    schema step as successful would be false diagnostics. Plain only, one call."""
    double = LadderDouble(lambda body, headers: False)
    result = run_ladder(double)

    assert len(double.attempts) == 1
    body, headers = double.attempts[0]['body'], double.attempts[0]['headers']
    assert body == {'temperature': 0}
    assert headers == {}
    assert result.params['structured_output_mode'] == judge.MODE_PLAIN
    assert result.params['structured_output_rejections'] == []
    assert judge.ladder_for(judge.API_FORMAT_ANTHROPIC) == (judge.MODE_PLAIN,)
    assert judge.ladder_for(judge.API_FORMAT_OPENAI) == (1, 2, 3)


@pytest.mark.parametrize('raw', ['false', '0', 'no', '', 'maybe'])
def test_anthropic_ladder_stays_plain_unless_the_flag_is_exactly_true(default_panel, raw):
    default_panel.setenv(judge.ENV_SONNET_STRUCTURED_OUTPUT, raw)
    assert judge.ladder_for(judge.API_FORMAT_ANTHROPIC) == (judge.MODE_PLAIN,)


@pytest.mark.parametrize('raw', ['true', 'TRUE', '1', 'yes'])
def test_anthropic_ladder_flag_switches_the_schema_step_on(default_panel, raw):
    default_panel.setenv(judge.ENV_SONNET_STRUCTURED_OUTPUT, raw)
    assert judge.ladder_for(judge.API_FORMAT_ANTHROPIC) == (judge.MODE_JSON_SCHEMA, judge.MODE_PLAIN)


def test_anthropic_step_one_sends_output_format_with_the_beta_header(structured_output_on):
    double = LadderDouble(lambda body, headers: False)
    result = run_ladder(double)

    assert len(double.attempts) == 1
    body, headers = double.attempts[0]['body'], double.attempts[0]['headers']
    assert body['temperature'] == 0
    assert body['output_format']['type'] == 'json_schema'
    assert body['output_format']['schema'] == judge.report_schema(LABELS)
    assert 'response_format' not in body
    assert headers == {'anthropic-beta': judge.ANTHROPIC_STRUCTURED_OUTPUTS_BETA}
    assert result.params['structured_output_mode'] == judge.MODE_JSON_SCHEMA
    assert result.params['structured_output_rejections'] == []


def test_anthropic_ladder_is_schema_then_plain_with_temperature_dropped_first(structured_output_on):
    double = LadderDouble(lambda body, headers: 'output_format' in body)
    result = run_ladder(double)

    shapes = [('output_format' in a['body'], 'temperature' in a['body'], bool(a['headers'])) for a in double.attempts]
    assert shapes == [(True, True, True), (True, False, True), (False, True, False)]
    # No json_object step exists for this format.
    assert all('response_format' not in a['body'] for a in double.attempts)
    assert result.params['structured_output_mode'] == judge.MODE_PLAIN
    assert [r['mode'] for r in result.params['structured_output_rejections']] == [1, 1]
    assert [r['temperature'] for r in result.params['structured_output_rejections']] == [True, False]
    assert (
        result.params['structured_output_rejections'][0]['message'] == 'output_format: Extra inputs are not permitted'
    )
    assert judge.parse_report(result.content, LABELS) == valid_report(LABELS)


def test_anthropic_plain_step_rejected_is_a_real_error(structured_output_on):
    double = LadderDouble(lambda body, headers: True)
    with pytest.raises(provider_client.UpstreamError):
        run_ladder(double)
    assert len(double.attempts) == 4  # schema+temp, schema, plain+temp, plain


def test_anthropic_plain_only_rejected_twice_is_a_real_error():
    double = LadderDouble(lambda body, headers: True)
    with pytest.raises(provider_client.UpstreamError):
        run_ladder(double)
    assert [('temperature' in a['body']) for a in double.attempts] == [True, False]


def test_anthropic_ladder_remembers_the_step_per_model(structured_output_on):
    double = LadderDouble(lambda body, headers: 'output_format' in body)
    run_ladder(double)
    again = LadderDouble(lambda body, headers: 'output_format' in body)
    run_ladder(again)
    assert len(again.attempts) == 1
    assert 'output_format' not in again.attempts[0]['body']


def test_a_cached_step_this_format_does_not_have_is_forgotten(structured_output_on):
    judge._MODE_CACHE[('sonnet', SONNET_MODEL)] = (judge.MODE_JSON_OBJECT, True)
    double = LadderDouble(lambda body, headers: False)
    result = run_ladder(double)
    assert double.attempts[0]['body']['output_format']['type'] == 'json_schema'
    assert result.params['structured_output_mode'] == judge.MODE_JSON_SCHEMA


def test_a_cached_schema_step_is_forgotten_once_the_flag_is_off(default_panel):
    """Flag switched off between calls: the remembered schema step is not on the
    ladder any more and must not be trusted."""
    judge._MODE_CACHE[('sonnet', SONNET_MODEL)] = (judge.MODE_JSON_SCHEMA, True)
    double = LadderDouble(lambda body, headers: False)
    result = run_ladder(double)
    assert 'output_format' not in double.attempts[0]['body']
    assert result.params['structured_output_mode'] == judge.MODE_PLAIN


def test_openai_judges_keep_their_three_step_ladder():
    double = LadderDouble(lambda body, headers: 'response_format' in body)
    result = run_ladder(double, provider_id='chatgpt')
    modes = [a['body'].get('response_format', {}).get('type') for a in double.attempts]
    assert modes == ['json_schema', 'json_schema', 'json_object', 'json_object', None]
    assert all(a['headers'] == {} for a in double.attempts)
    assert all('output_format' not in a['body'] for a in double.attempts)
    assert result.params['structured_output_mode'] == judge.MODE_PLAIN


def test_call_judge_picks_the_anthropic_client_for_sonnet_by_default(default_panel):
    double = install_double(default_panel)
    double.responder = lambda body, request: messages_response(FENCED_REPORT)

    result = asyncio.run(judge.call_judge('sonnet', SONNET_HOST, DUMMY_KEY, SONNET_MODEL, MESSAGES, LABELS))

    assert double.requests[-1]['url'] == f'{SONNET_HOST}/v1/messages'
    assert double.requests[-1]['headers']['anthropic-version'] == '2023-06-01'
    assert 'response_format' not in double.last
    assert result.params['max_tokens'] == 4096
    assert result.params['output_tokens'] == 321


####################
# 6 — the router: judging with sonnet
####################


def test_judge_endpoint_sonnet_full_path(default_panel):
    configure_sonnet(default_panel)
    double = install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/sonnet')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == STATUS_COMPLETE
    assert payload['judge'] == 'sonnet'
    assert payload['revision'] == 1
    assert payload['requested_model'] == SONNET_MODEL
    assert payload['model'] == 'claude-4.6-sonnet'
    assert sorted(payload['label_map']) == LABELS
    assert sorted(payload['label_map'].values()) == sorted(providers.PROVIDER_IDS)
    assert payload['error'] is None
    # Kie default: plain, honestly recorded as plain; no beta header went out.
    assert payload['params']['structured_output_mode'] == judge.MODE_PLAIN
    assert payload['params']['structured_output_rejections'] == []
    assert payload['params']['max_tokens'] == 4096
    assert payload['params']['output_tokens'] == 321
    assert 'headers_sent' not in payload['params']
    assert payload['mapped']['verdict']['kind'] == 'winner'

    sent = double.requests[-1]
    assert sent['url'] == f'{SONNET_HOST}/v1/messages'
    assert sent['body']['model'] == SONNET_MODEL
    assert 'output_format' not in sent['body']
    assert 'anthropic-beta' not in sent['headers']
    assert len(double.requests) == 1
    assert 'system' in sent['body']
    assert all(m['role'] == 'user' for m in sent['body']['messages'])
    assert DUMMY_KEY not in response.text


def test_judge_endpoint_sonnet_walks_to_plain_and_unwraps_the_fence(structured_output_on):
    default_panel = structured_output_on
    configure_sonnet(default_panel)
    double = install_double(default_panel)

    def responder(body, request):
        if 'output_format' in body:
            return anthropic_error(400, 'invalid_request_error', 'output_format: Extra inputs are not permitted')
        return messages_response(f'```json\n{json.dumps(valid_report(labels_in(body)))}\n```')

    double.responder = responder
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/sonnet')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == STATUS_COMPLETE
    assert payload['params']['structured_output_mode'] == judge.MODE_PLAIN
    assert [r['mode'] for r in payload['params']['structured_output_rejections']] == [1, 1]
    assert 'headers_sent' not in payload['params']
    assert len(double.requests) == 3


def test_judge_endpoint_sonnet_truncation_fails_the_row_retryably(default_panel):
    configure_sonnet(default_panel)
    double = install_double(default_panel)
    double.responder = lambda body, request: messages_response('{"answers": [', stop_reason='max_tokens')
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/sonnet')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == STATUS_FAILED
    assert payload['error']['code'] == 'truncated'
    assert payload['report'] is None
    assert payload['params']['output_tokens'] == 321
    assert payload['params']['max_tokens'] == 4096
    rows = stored_reports(run_id)
    assert [(r.judge, r.status) for r in rows] == [('sonnet', STATUS_FAILED)]
    assert rows[0].params['output_tokens'] == 321


def test_judge_endpoint_refuses_a_participant_by_default_and_calls_nothing(default_panel):
    configure_sonnet(default_panel)
    double = install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/chatgpt')

    assert response.status_code == 400
    assert response.json()['detail'] == {'code': 'judge_not_capable', 'judge': 'chatgpt'}
    assert double.requests == []
    assert stored_reports(run_id) == []


def test_judge_endpoint_refuses_an_unknown_judge_before_capability(default_panel):
    run_id = make_run()
    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/claude')
    assert response.status_code == 400
    assert response.json()['detail'] == {'code': 'unknown_judge', 'judge': 'claude'}


def test_judge_endpoint_sonnet_unconfigured_is_a_precondition(default_panel):
    double = install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/sonnet')

    assert response.status_code == 503
    detail = response.json()['detail']
    assert detail == {
        'code': 'not_configured',
        'provider': 'sonnet',
        'missing': ['ANSWER_COMPARE_SONNET_API_KEY', 'ANSWER_COMPARE_SONNET_MODEL'],
    }
    assert double.requests == []
    assert stored_reports(run_id) == []


def test_run_all_runs_sonnet_only(default_panel):
    configure_sonnet(default_panel)
    for provider_id in providers.PROVIDER_IDS:
        default_panel.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_BASE_URL', f'https://{provider_id}.example/v1')
        default_panel.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_API_KEY', DUMMY_KEY)
        default_panel.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_MODEL', ANSWER_MODEL[provider_id])
    double = install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/reports')

    assert response.status_code == 200, response.text
    results = response.json()['results']
    assert [r['judge'] for r in results] == ['sonnet']
    assert results[0]['status'] == STATUS_COMPLETE
    assert [r['url'] for r in double.requests] == [f'{SONNET_HOST}/v1/messages']
    assert [r.judge for r in stored_reports(run_id)] == ['sonnet']


####################
# 7 — GET, tally and summary with the single independent judge
####################


def test_get_run_shows_one_capable_judge_card_by_default(default_panel):
    configure_sonnet(default_panel)
    run_id = make_run()
    seed_answers(run_id)

    payload = client_as().get(f'/api/v1/compare/runs/{run_id}').json()

    assert [(r['judge'], r['capable']) for r in payload['reports']] == [('sonnet', True)]
    assert [a['provider'] for a in payload['answers']] == list(providers.PROVIDER_IDS)
    # The judge registry rides along with `providers`, for the page's judge cards.
    assert [p['id'] for p in payload['providers']] == list(providers.PROVIDER_IDS)
    assert [(j['id'], j['can_judge']) for j in payload['judges']] == [
        ('chatgpt', False),
        ('gemini', False),
        ('vesqor', False),
        ('sonnet', True),
    ]
    assert payload['judges'][3]['configured'] is True
    created = client_as().post('/api/v1/compare/runs', json={'prompt': 'x'}).json()
    assert [j['id'] for j in created['judges']] == list(providers.JUDGE_CANDIDATE_IDS)
    assert [p['id'] for p in created['providers']] == list(providers.PROVIDER_IDS)
    assert payload['tally']['n_judges'] == 1
    assert payload['tally']['excluded'] == [{'judge': 'sonnet', 'reason': 'no_report'}]


def test_single_sonnet_verdict_is_preferred_and_not_partial(default_panel):
    configure_sonnet(default_panel)
    install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()
    assert api.post(f'/api/v1/compare/runs/{run_id}/reports/sonnet').status_code == 200

    payload = api.get(f'/api/v1/compare/runs/{run_id}').json()
    run_tally = payload['tally']

    assert run_tally['outcome']['kind'] == 'preferred'
    assert run_tally['outcome']['provider'] in providers.PROVIDER_IDS
    assert run_tally['n_included'] == 1
    assert run_tally['n_judges'] == 1
    assert run_tally['partial'] is False
    assert run_tally['included_judges'] == ['sonnet']
    assert run_tally['excluded'] == []
    assert run_tally['self_votes'] == []
    assert run_tally['self_votes_excluded'] == []
    # Votes are per ANSWER provider: sonnet never appears as a column.
    assert tuple(run_tally['votes']) == providers.PROVIDER_IDS
    assert sum(run_tally['votes'].values()) == 1
    assert run_tally['verdicts'] == [
        {'judge': 'sonnet', 'kind': 'winner', 'providers': [run_tally['outcome']['provider']]}
    ]


def test_pure_tally_single_sonnet_verdict(default_panel):
    versions = [{'provider': p, 'revision': 1} for p in providers.PROVIDER_IDS]
    label_map = dict(zip(LABELS, providers.PROVIDER_IDS))
    report = tally.TallyReport(
        revision=1, judged_versions=versions, label_map=label_map, report=valid_report(LABELS, 2)
    )

    result = tally.compute_tally(versions, [tally.TallyJudge(judge='sonnet', current=report)])

    assert result['outcome'] == {'kind': 'preferred', 'provider': 'vesqor'}
    assert result['partial'] is False
    assert result['n_judges'] == 1
    assert result['self_votes'] == []
    assert list(result['votes']) == list(providers.PROVIDER_IDS)
    assert result['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 1}


def test_summary_with_one_sonnet_report_has_no_agreement_section_and_no_two_judge_sentence(default_panel):
    versions = [{'provider': p, 'revision': 1} for p in providers.PROVIDER_IDS]
    label_map = dict(zip(LABELS, providers.PROVIDER_IDS))
    raw = valid_report(LABELS, winner_index=2, improvements_for='C')  # C -> vesqor
    report = tally.TallyReport(revision=1, judged_versions=versions, label_map=label_map, report=raw)
    run_tally = tally.compute_tally(versions, [tally.TallyJudge(judge='sonnet', current=report)])

    result = summary.build_summary(run_tally, [summary.SummaryReport(judge='sonnet', label_map=label_map, report=raw)])
    text = result.narrative

    assert 'Preferred answer: VESQOR (1 of 1 judges).' in text
    assert summary.HEADING_AGREEMENT not in text
    assert 'requires at least two' not in text
    assert 'Partial summary' not in text
    assert result.partial is False
    assert result.included_judges == ['sonnet']
    # §2: the verdict and its rationale, attributed to Sonnet.
    assert 'Sonnet: winner — VESQOR (was C).' in text
    assert 'The winner states the trade-off and names a measurement.' in text
    # §8: the improvements for the owner's answer, attributed to Sonnet.
    assert summary.HEADING_IMPROVEMENTS in text
    assert 'Sonnet: "p99 latency" — say which percentile and why' in text
    # §9: the claims, attributed to Sonnet.
    assert 'Sonnet: the p99 figure' in text


def test_summary_endpoint_with_sonnet_alone(default_panel):
    configure_sonnet(default_panel)
    install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()
    assert api.post(f'/api/v1/compare/runs/{run_id}/reports/sonnet').status_code == 200

    response = api.post(f'/api/v1/compare/runs/{run_id}/summary')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['partial'] is False
    assert payload['included_judges'] == ['sonnet']
    assert payload['outdated'] is False
    assert 'Preferred answer:' in payload['narrative']
    assert summary.HEADING_AGREEMENT not in payload['narrative']


####################
# 8 — legacy participant reports stay visible, excluded with a reason
####################


def test_legacy_participant_report_is_visible_excluded_and_outdates_its_summary(default_panel):
    """A run judged by chatgpt before #016, read after it.

    The report row exists and must stay on the page (capable: false), the tally
    must exclude it with a reason rather than drop it, and the summary that was
    built on it is now outdated because the included reports moved.
    """
    configure_sonnet(default_panel)
    run_id = make_run()
    seed_answers(run_id)
    legacy = seed_legacy_report(run_id, 'chatgpt')
    versions = asyncio.run(AnswerCompareAnswers.get_current_versions(run_id))
    asyncio.run(
        AnswerCompareSummaries.insert_next_revision(
            run_id=run_id,
            narrative='built when chatgpt judged',
            tally={'included_reports': [{'judge': 'chatgpt', 'revision': legacy.revision}]},
            judged_versions=[{'provider': v.provider, 'revision': v.revision} for v in versions],
            partial=False,
            included_judges=['chatgpt'],
        )
    )

    payload = client_as().get(f'/api/v1/compare/runs/{run_id}').json()

    reports = {r['judge']: r for r in payload['reports']}
    assert list(reports) == ['chatgpt', 'sonnet']
    assert reports['chatgpt']['capable'] is False
    assert reports['chatgpt']['current']['status'] == STATUS_COMPLETE
    assert reports['chatgpt']['current']['revision'] == legacy.revision
    assert reports['chatgpt']['outdated'] is False
    assert reports['sonnet']['capable'] is True
    assert reports['sonnet']['current'] is None

    run_tally = payload['tally']
    assert run_tally['n_judges'] == 1
    assert run_tally['n_included'] == 0
    assert run_tally['partial'] is True
    assert run_tally['excluded'] == [
        {'judge': 'chatgpt', 'reason': 'not_capable'},
        {'judge': 'sonnet', 'reason': 'no_report'},
    ]
    # The legacy self-vote (chatgpt named vesqor here, so none) is not invented.
    assert run_tally['self_votes_excluded'] == []
    assert run_tally['outcome']['kind'] == 'no_valid_verdicts'

    assert payload['summary']['current']['outdated'] is True
    assert payload['summary']['current']['included_judges'] == ['chatgpt']


def test_legacy_report_line_appears_in_a_new_summary_beside_sonnet(default_panel):
    configure_sonnet(default_panel)
    install_double(default_panel)
    run_id = make_run()
    seed_answers(run_id)
    seed_legacy_report(run_id, 'gemini')
    api = client_as()
    assert api.post(f'/api/v1/compare/runs/{run_id}/reports/sonnet').status_code == 200

    response = api.post(f'/api/v1/compare/runs/{run_id}/summary')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['partial'] is False
    assert payload['included_judges'] == ['sonnet']
    assert '- Gemini: participant judge, no longer used.' in payload['narrative']
    assert 'Partial summary' not in payload['narrative']


def test_summary_refuses_when_only_a_legacy_report_exists(default_panel):
    run_id = make_run()
    seed_answers(run_id)
    seed_legacy_report(run_id, 'chatgpt')

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/summary')

    assert response.status_code == 409
    detail = response.json()['detail']
    assert detail['code'] == 'no_verdicts_to_summarise'
    assert {'judge': 'chatgpt', 'reason': 'not_capable'} in detail['excluded']
