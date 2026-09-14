"""VQ-25 stage 2a: generation — the client, the endpoints, and the failure paths.

Separate from `test_vq25_answer_compare.py` (stage 1) because the two need
different machinery: everything here drives HTTP through `TestClient` against a
provider double, and stage 1's file is about configuration and persistence.

Run from the repository root:  pytest test/test_vq25_generation.py -q

No `pytest-asyncio` (not installed, must not be installed): async code is driven
from synchronous tests with `asyncio.run(...)`, and everything touching the
database runs inside one `asyncio.run` per test so no pooled aiosqlite
connection outlives its event loop.

Provider doubles are `httpx.MockTransport` installed over the client module's own
`_build_async_client` seam, so every test exercises the real request building,
status translation and response parsing. Nothing in the application can fabricate
an answer.
"""

import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.answer_compare import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    AnswerCompareAnswer,
    AnswerCompareAnswers,
    AnswerCompareReports,
    AnswerCompareRun,
    AnswerCompareRunForm,
    AnswerCompareRuns,
    AnswerCompareSummary,
    AnswerCompareReport,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils.auth import get_current_user
from sqlalchemy import select

DUMMY_KEY = 'sk-vq25-generation-secret'
PROMPT = 'Explain the trade-off between throughput and latency.'
REFERENCE = 'Latency is per-request; throughput is per-unit-time.'

PROVIDER_ENV = {
    'chatgpt': ('ANSWER_COMPARE_CHATGPT_BASE_URL', 'ANSWER_COMPARE_CHATGPT_API_KEY', 'ANSWER_COMPARE_CHATGPT_MODEL'),
    'gemini': ('ANSWER_COMPARE_GEMINI_BASE_URL', 'ANSWER_COMPARE_GEMINI_API_KEY', 'ANSWER_COMPARE_GEMINI_MODEL'),
    'vesqor': ('ANSWER_COMPARE_VESQOR_BASE_URL', 'ANSWER_COMPARE_VESQOR_API_KEY', 'ANSWER_COMPARE_VESQOR_MODEL'),
}

PROVIDER_HOST = {
    'chatgpt': 'https://chatgpt.example/v1',
    'gemini': 'https://gemini.example/v1',
    'vesqor': 'https://door.example/api/v1',
}

PROVIDER_MODEL = {
    'chatgpt': 'gpt-test-1',
    'gemini': 'gemini-test-1',
    'vesqor': 'vesqor-reasoning',
}


####################
# Fixtures and doubles
####################


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Nothing ambient reaches the resolver, and the tables exist."""
    for names in PROVIDER_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'VESQOR_SERVICE_TOKEN', 'VESQOR_API_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    for provider_id in providers.PROVIDER_IDS:
        monkeypatch.delenv(providers.max_input_chars_env(provider_id), raising=False)
    monkeypatch.delenv(provider_client.ENV_REQUEST_TIMEOUT_SECONDS, raising=False)

    asyncio.run(_create_tables())
    return monkeypatch


def configure(monkeypatch, *provider_ids: str) -> None:
    for provider_id in provider_ids:
        base_url_env, key_env, model_env = PROVIDER_ENV[provider_id]
        monkeypatch.setenv(base_url_env, PROVIDER_HOST[provider_id])
        monkeypatch.setenv(key_env, DUMMY_KEY)
        monkeypatch.setenv(model_env, PROVIDER_MODEL[provider_id])


class ProviderDouble:
    """Records every request it receives and replies as configured."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: dict[str, object] = {}

    def reply_with(self, host_prefix: str, response):
        self.responses[host_prefix] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                'url': str(request.url),
                'headers': dict(request.headers),
                'body': json.loads(request.content.decode()),
            }
        )
        for prefix, response in self.responses.items():
            if str(request.url).startswith(prefix):
                if callable(response):
                    return response(request)
                return response
        return _completion('default answer')

    def bodies_for(self, host_prefix: str) -> list[dict]:
        return [r['body'] for r in self.requests if r['url'].startswith(host_prefix)]


def _completion(text: str, model: str = 'resolved-model-2026-01-01', headers=None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            'model': model,
            'choices': [{'message': {'role': 'assistant', 'content': text}}],
        },
        headers=headers or {},
    )


def install_double(monkeypatch) -> ProviderDouble:
    double = ProviderDouble()

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout)

    monkeypatch.setattr(provider_client, '_build_async_client', build)
    return double


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

    tables = [
        AnswerCompareRun.__table__,
        AnswerCompareAnswer.__table__,
        AnswerCompareReport.__table__,
        AnswerCompareSummary.__table__,
    ]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


async def _stored_answers(run_id: str) -> list[dict]:
    """Read the answer table directly — the append-only rule is about storage."""
    from open_webui.internal.db import get_async_db_context

    async with get_async_db_context() as db:
        result = await db.execute(
            select(AnswerCompareAnswer)
            .where(AnswerCompareAnswer.run_id == run_id)
            .order_by(AnswerCompareAnswer.provider, AnswerCompareAnswer.revision)
        )
        return [
            {
                'id': row.id,
                'provider': row.provider,
                'revision': row.revision,
                'status': row.status,
                'text': row.text,
                'model': row.model,
                'requested_model': row.requested_model,
                'engine_version': row.engine_version,
                'params': row.params,
                'error': row.error,
            }
            for row in result.scalars().all()
        ]


def make_run(prompt: str = PROMPT, reference: str = REFERENCE) -> str:
    async def _run():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email='admin@example.com',
            form=AnswerCompareRunForm(prompt=prompt, reference=reference),
        )
        return run.id

    return asyncio.run(_run())


def read_answers(run_id: str) -> list[dict]:
    return asyncio.run(_stored_answers(run_id))


####################
# 1 — all three generate, with the metadata persisted
####################


def test_all_three_providers_generate_and_persist_metadata(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('chatgpt answer', model='gpt-test-1-2026-01-01'))
    double.reply_with(PROVIDER_HOST['gemini'], _completion('gemini answer', model='gemini-test-1-002'))
    double.reply_with(
        PROVIDER_HOST['vesqor'],
        _completion('vesqor answer', model='vesqor-reasoning', headers={'x-vesqor-engine-version': '4.2.1'}),
    )

    run_id = make_run()
    api = client_as()

    for provider_id in ('chatgpt', 'gemini', 'vesqor'):
        response = api.post(f'/api/v1/compare/runs/{run_id}/answers/{provider_id}')
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['status'] == STATUS_COMPLETE
        assert payload['provider'] == provider_id
        assert payload['revision'] == 1
        assert payload['requested_model'] == PROVIDER_MODEL[provider_id]
        assert payload['params'] == {'stream': False}
        assert payload['error'] is None

    stored = {row['provider']: row for row in read_answers(run_id)}
    assert stored['chatgpt']['text'] == 'chatgpt answer'
    # The reported model differs from the requested one; both are kept.
    assert stored['chatgpt']['requested_model'] == 'gpt-test-1'
    assert stored['chatgpt']['model'] == 'gpt-test-1-2026-01-01'
    assert stored['gemini']['model'] == 'gemini-test-1-002'
    assert stored['vesqor']['engine_version'] == '4.2.1'
    # No version reported means none recorded — never invented.
    assert stored['chatgpt']['engine_version'] is None
    assert stored['gemini']['engine_version'] is None


def test_engine_version_from_body_when_no_header(isolated_env):
    configure(isolated_env, 'vesqor')
    double = install_double(isolated_env)
    double.reply_with(
        PROVIDER_HOST['vesqor'],
        httpx.Response(
            200,
            json={
                'model': 'vesqor-reasoning',
                'choices': [{'message': {'content': 'answer'}}],
                'vq_meta': {'engine_version': '5.0.0-rc1'},
            },
        ),
    )

    run_id = make_run()
    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/vesqor')

    assert response.status_code == 200
    assert response.json()['engine_version'] == '5.0.0-rc1'


####################
# 2 — partial failure leaves the other providers alone
####################


@pytest.mark.parametrize(
    ('failure', 'expected_code'),
    [
        (httpx.Response(500, json={'error': {'message': 'boom'}}), 'upstream_error'),
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('too slow')), 'timeout'),
        (httpx.Response(200, text='not json at all'), 'malformed_response'),
        (httpx.Response(401, json={'error': {'message': 'bad key'}}), 'auth'),
        (httpx.Response(429, json={'error': {'message': 'slow down'}}), 'rate_limit'),
    ],
)
def test_one_provider_failing_does_not_touch_the_others(isolated_env, failure, expected_code):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('chatgpt answer'))
    double.reply_with(PROVIDER_HOST['vesqor'], _completion('vesqor answer'))
    double.reply_with(PROVIDER_HOST['gemini'], failure)

    run_id = make_run()
    api = client_as()

    for provider_id in ('chatgpt', 'gemini', 'vesqor'):
        assert api.post(f'/api/v1/compare/runs/{run_id}/answers/{provider_id}').status_code == 200

    stored = {row['provider']: row for row in read_answers(run_id)}
    assert len(stored) == 3

    assert stored['gemini']['status'] == STATUS_FAILED
    assert stored['gemini']['text'] is None
    assert json.loads(stored['gemini']['error'])['code'] == expected_code

    # Untouched by the neighbour's failure.
    assert stored['chatgpt']['status'] == STATUS_COMPLETE
    assert stored['chatgpt']['text'] == 'chatgpt answer'
    assert stored['chatgpt']['error'] is None
    assert stored['vesqor']['status'] == STATUS_COMPLETE
    assert stored['vesqor']['text'] == 'vesqor answer'
    assert stored['vesqor']['error'] is None


####################
# 3 — concurrency: the browser fires all three at once
####################


def test_three_providers_generate_concurrently(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    for provider_id in providers.PROVIDER_IDS:
        double.reply_with(PROVIDER_HOST[provider_id], _completion(f'{provider_id} answer'))

    run_id = make_run()

    async def _fire_all():
        transport = httpx.ASGITransport(app=_asgi_app())
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as api:
            return await asyncio.gather(
                *(api.post(f'/api/v1/compare/runs/{run_id}/answers/{p}') for p in providers.PROVIDER_IDS)
            )

    responses = asyncio.run(_fire_all())
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert sorted(r.json()['provider'] for r in responses) == ['chatgpt', 'gemini', 'vesqor']

    stored = read_answers(run_id)
    assert len(stored) == 3
    assert {row['provider'] for row in stored} == set(providers.PROVIDER_IDS)
    assert {row['revision'] for row in stored} == {1}
    assert all(row['status'] == STATUS_COMPLETE for row in stored)


def test_same_provider_fired_twice_concurrently_keeps_both_revisions(isolated_env):
    """The unique index makes the collision visible; the retry must resolve it."""
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('chatgpt answer'))

    run_id = make_run()

    async def _fire_twice():
        return await asyncio.gather(
            *(
                AnswerCompareAnswers.insert_next_revision(
                    run_id=run_id,
                    provider='chatgpt',
                    status=STATUS_PENDING,
                )
                for _ in range(2)
            )
        )

    inserted = asyncio.run(_fire_twice())

    assert sorted(row.revision for row in inserted) == [1, 2]
    stored = read_answers(run_id)
    assert [row['revision'] for row in stored] == [1, 2]


def _asgi_app():
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user('admin')
    return app


def test_pending_row_exists_while_the_provider_call_is_in_flight(isolated_env):
    """The row is written BEFORE the call, not after it.

    A three-minute request dropped by a proxy would otherwise discard a
    completion that was already paid for. The double reads the table from inside
    the request to prove the row is already there.
    """
    configure(isolated_env, 'chatgpt')
    run_holder: dict = {}
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen['rows'] = await _stored_answers(run_holder['id'])
        return _completion('answer')

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    isolated_env.setattr(provider_client, '_build_async_client', build)

    run_holder['id'] = make_run()
    response = client_as().post(f'/api/v1/compare/runs/{run_holder["id"]}/answers/chatgpt')

    assert response.status_code == 200
    assert response.json()['status'] == STATUS_COMPLETE

    in_flight = seen['rows']
    assert len(in_flight) == 1
    assert in_flight[0]['status'] == STATUS_PENDING
    assert in_flight[0]['requested_model'] == PROVIDER_MODEL['chatgpt']
    assert in_flight[0]['text'] is None

    # The same row was settled in place, not replaced by a second one.
    settled = read_answers(run_holder['id'])
    assert len(settled) == 1
    assert settled[0]['id'] == in_flight[0]['id']
    assert settled[0]['status'] == STATUS_COMPLETE


####################
# 4 — a failed retry must not become "current" and must not outdate the judging
####################


def test_failed_retry_does_not_replace_the_current_answer_or_outdate_reports(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()
    api = client_as()

    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('first good answer'))
    assert api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()['revision'] == 1

    async def _versions():
        return await AnswerCompareAnswers.get_current_versions(run_id)

    versions_after_success = [(v.provider, v.revision) for v in asyncio.run(_versions())]
    assert versions_after_success == [('chatgpt', 1)]

    # A judge reports on exactly that answer set.
    async def _judge():
        return await AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge='gemini',
            status=STATUS_COMPLETE,
            judged_versions=[{'provider': 'chatgpt', 'revision': 1}],
        )

    report = asyncio.run(_judge())

    # Revision 2 fails.
    double.reply_with(PROVIDER_HOST['chatgpt'], httpx.Response(500, json={}))
    failed = api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()
    assert failed['revision'] == 2
    assert failed['status'] == STATUS_FAILED

    async def _state():
        return (
            await AnswerCompareAnswers.get_current_by_run(run_id),
            await AnswerCompareAnswers.get_current_versions(run_id),
            await AnswerCompareAnswers.get_latest_attempt_by_run(run_id),
        )

    current, versions, latest = asyncio.run(_state())

    # The last good answer is still the current one.
    assert [a.revision for a in current] == [1]
    assert current[0].text == 'first good answer'
    # The failed attempt is visible separately, and kept.
    assert [a.revision for a in latest] == [2]
    assert latest[0].status == STATUS_FAILED
    # The judging did NOT become outdated.
    assert [{'provider': v.provider, 'revision': v.revision} for v in versions] == report.judged_versions

    # A successful revision 3 does move the current answer — and now the report is outdated.
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('third answer'))
    assert api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()['revision'] == 3

    current, versions, _ = asyncio.run(_state())
    assert [a.revision for a in current] == [3]
    assert current[0].text == 'third answer'
    assert [{'provider': v.provider, 'revision': v.revision} for v in versions] != report.judged_versions


def test_update_result_refuses_to_overwrite_a_settled_answer(isolated_env):
    """A complete answer is never overwritten, whoever calls with its id.

    Unreachable through today's single caller, which only ever passes the id of
    the pending row it just inserted — but "nothing overwrites an existing
    answer" has to hold in the table rather than in the caller's discipline,
    and stage 3 adds callers.
    """
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('the good answer', model='gpt-test-1-2026-01-01'))

    run_id = make_run()
    complete = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()
    assert complete['status'] == STATUS_COMPLETE

    async def _try_to_clobber():
        return await AnswerCompareAnswers.update_result(
            id=complete['id'],
            status=STATUS_FAILED,
            error=json.dumps({'code': 'timeout', 'message': 'should never be written'}),
        )

    refused = asyncio.run(_try_to_clobber())
    assert refused is None

    stored = read_answers(run_id)
    assert len(stored) == 1
    assert stored[0]['id'] == complete['id']
    assert stored[0]['status'] == STATUS_COMPLETE
    assert stored[0]['text'] == 'the good answer'
    assert stored[0]['model'] == 'gpt-test-1-2026-01-01'
    assert stored[0]['error'] is None


def test_update_result_refuses_to_overwrite_a_failed_answer(isolated_env):
    """The same guard, from the other settled state."""
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], httpx.Response(500, json={}))

    run_id = make_run()
    failed = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()
    assert failed['status'] == STATUS_FAILED

    async def _try_to_clobber():
        return await AnswerCompareAnswers.update_result(
            id=failed['id'],
            status=STATUS_COMPLETE,
            text='a completion that never happened',
        )

    assert asyncio.run(_try_to_clobber()) is None

    stored = read_answers(run_id)
    assert stored[0]['status'] == STATUS_FAILED
    assert stored[0]['text'] is None
    assert json.loads(stored[0]['error'])['code'] == 'upstream_error'


def test_update_result_returns_none_for_a_missing_row(isolated_env):
    async def _missing():
        return await AnswerCompareAnswers.update_result(id='no-such-answer', status=STATUS_COMPLETE)

    assert asyncio.run(_missing()) is None


####################
# 5 — regeneration keeps every earlier row
####################


def test_regeneration_inserts_the_next_revision_and_keeps_the_old_rows(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()
    api = client_as()

    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('answer one'))
    first = api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('answer two'))
    second = api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()

    stored = read_answers(run_id)
    assert [row['revision'] for row in stored] == [1, 2]
    assert stored[0]['id'] == first['id']
    assert stored[0]['text'] == 'answer one'
    assert stored[1]['id'] == second['id']
    assert stored[1]['text'] == 'answer two'


####################
# 6 — oversized input: refused before anything is sent
####################


def test_oversized_input_is_refused_and_nothing_is_sent(isolated_env):
    configure(isolated_env, 'chatgpt')
    isolated_env.setenv('ANSWER_COMPARE_CHATGPT_MAX_INPUT_CHARS', '50')
    double = install_double(isolated_env)

    run_id = make_run(prompt='x' * 80, reference='y' * 10)
    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')

    assert response.status_code == 413
    detail = response.json()['detail']
    assert detail['code'] == 'oversized'
    assert detail['provider'] == 'chatgpt'
    assert detail['limit_chars'] == 50
    assert detail['actual_chars'] == 90

    assert double.requests == []
    assert read_answers(run_id) == []


def test_run_creation_reports_input_size_without_failing(isolated_env):
    configure(isolated_env, 'chatgpt')
    isolated_env.setenv('ANSWER_COMPARE_CHATGPT_MAX_INPUT_CHARS', '10')

    response = client_as().post('/api/v1/compare/runs', json={'prompt': 'x' * 40, 'reference': 'y' * 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload['input_size']['chars'] == 50
    by_provider = {p['provider']: p for p in payload['input_size']['per_provider']}
    assert by_provider['chatgpt'] == {'provider': 'chatgpt', 'limit_chars': 10, 'exceeds': True}
    assert by_provider['gemini']['exceeds'] is False
    assert by_provider['gemini']['limit_chars'] == providers.DEFAULT_MAX_INPUT_CHARS


####################
# 7 — context_length_exceeded is its own code, distinct from the pre-flight 413
####################


def test_upstream_context_length_is_its_own_error_code(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(
        PROVIDER_HOST['chatgpt'],
        httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'too long'}}),
    )

    run_id = make_run()
    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')

    # 200 with a failed row — the call happened, the provider refused it.
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == STATUS_FAILED
    assert payload['error']['code'] == 'context_length_exceeded'
    # Distinct from the pre-flight limit, which never reaches the provider.
    assert payload['error']['code'] != 'oversized'


def test_generic_bad_request_is_not_reported_as_context_length(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], httpx.Response(400, json={'error': {'message': 'bad parameter'}}))

    run_id = make_run()
    payload = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').json()

    assert payload['status'] == STATUS_FAILED
    assert payload['error']['code'] == 'upstream_error'


####################
# 8 — unconfigured, already running, stale pending
####################


def test_unconfigured_provider_returns_503_and_writes_no_row(isolated_env):
    configure(isolated_env, 'chatgpt')
    isolated_env.delenv('ANSWER_COMPARE_CHATGPT_MODEL', raising=False)
    double = install_double(isolated_env)

    run_id = make_run()
    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')

    assert response.status_code == 503
    detail = response.json()['detail']
    assert detail['code'] == 'not_configured'
    assert detail['provider'] == 'chatgpt'
    assert detail['missing'] == ['ANSWER_COMPARE_CHATGPT_MODEL']

    assert double.requests == []
    assert read_answers(run_id) == []


def test_second_call_while_pending_returns_409_and_sends_nothing(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()

    async def _pending():
        return await AnswerCompareAnswers.insert_next_revision(
            run_id=run_id,
            provider='chatgpt',
            status=STATUS_PENDING,
        )

    pending = asyncio.run(_pending())

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')

    assert response.status_code == 409
    detail = response.json()['detail']
    assert detail['code'] == 'already_running'
    assert detail['provider'] == 'chatgpt'
    assert detail['since'] == pending.created_at

    assert double.requests == []
    assert [row['revision'] for row in read_answers(run_id)] == [1]


def test_stale_pending_reads_as_failed(isolated_env):
    configure(isolated_env, 'chatgpt')
    install_double(isolated_env)
    run_id = make_run()

    async def _stale():
        row = await AnswerCompareAnswers.insert_next_revision(
            run_id=run_id,
            provider='chatgpt',
            status=STATUS_PENDING,
        )
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            stored = await db.get(AnswerCompareAnswer, row.id)
            stored.created_at = int(time.time()) - int(provider_client.request_timeout_seconds()) - 60
            await db.commit()
        return row.id

    stale_id = asyncio.run(_stale())

    payload = client_as().get(f'/api/v1/compare/runs/{run_id}').json()
    chatgpt = next(a for a in payload['answers'] if a['provider'] == 'chatgpt')

    assert chatgpt['current'] is None
    assert chatgpt['latest_attempt']['id'] == stale_id
    assert chatgpt['latest_attempt']['status'] == STATUS_FAILED
    assert chatgpt['latest_attempt']['error']['code'] == 'stale'

    # Read-time rule only: the stored row is untouched.
    assert [row['status'] for row in read_answers(run_id)] == [STATUS_PENDING]


def test_stale_pending_does_not_block_a_new_generation(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('recovered'))
    run_id = make_run()

    async def _stale():
        row = await AnswerCompareAnswers.insert_next_revision(run_id=run_id, provider='chatgpt', status=STATUS_PENDING)
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            stored = await db.get(AnswerCompareAnswer, row.id)
            stored.created_at = int(time.time()) - int(provider_client.request_timeout_seconds()) - 60
            await db.commit()

    asyncio.run(_stale())

    response = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')
    assert response.status_code == 200
    assert response.json()['revision'] == 2


####################
# 9 — the admin gate
####################


def test_generation_endpoints_reject_a_non_admin(isolated_env):
    configure(isolated_env, 'chatgpt')
    install_double(isolated_env)
    run_id = make_run()
    api = client_as('user')

    assert api.post('/api/v1/compare/runs', json={'prompt': 'hi'}).status_code == 401
    assert api.get(f'/api/v1/compare/runs/{run_id}').status_code == 401
    assert api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').status_code == 401


####################
# 10 — identical payloads for every provider
####################


def test_every_provider_receives_a_byte_identical_payload(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    for provider_id in providers.PROVIDER_IDS:
        double.reply_with(PROVIDER_HOST[provider_id], _completion('answer'))

    run_id = make_run()
    api = client_as()
    for provider_id in providers.PROVIDER_IDS:
        assert api.post(f'/api/v1/compare/runs/{run_id}/answers/{provider_id}').status_code == 200

    messages = [double.bodies_for(PROVIDER_HOST[p])[0]['messages'] for p in providers.PROVIDER_IDS]

    # Byte-identical message lists across all three.
    assert messages[0] == messages[1] == messages[2]
    # Exactly one user message, no system prompt of any kind.
    for message_list in messages:
        assert [m['role'] for m in message_list] == ['user']
    content = messages[0][0]['content']
    assert PROMPT in content
    assert REFERENCE in content

    # Only the model differs, because only the model is per-provider.
    bodies = [double.bodies_for(PROVIDER_HOST[p])[0] for p in providers.PROVIDER_IDS]
    assert [b['model'] for b in bodies] == [PROVIDER_MODEL[p] for p in providers.PROVIDER_IDS]
    for body in bodies:
        assert body['stream'] is False
        assert set(body) == {'model', 'messages', 'stream'}


def test_prompt_alone_is_sent_verbatim_without_reference_scaffolding(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('answer'))

    run_id = make_run(prompt=PROMPT, reference='')
    assert client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt').status_code == 200

    content = double.bodies_for(PROVIDER_HOST['chatgpt'])[0]['messages'][0]['content']
    assert content == PROMPT


####################
# 11 — no key material anywhere
####################


def test_no_key_material_in_stored_rows_or_responses(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('ok'))
    double.reply_with(PROVIDER_HOST['gemini'], httpx.Response(401, json={'error': {'message': 'bad key'}}))
    double.reply_with(PROVIDER_HOST['vesqor'], httpx.Response(500, json={'error': {'message': 'boom'}}))

    run_id = make_run()
    api = client_as()
    payloads = [api.post(f'/api/v1/compare/runs/{run_id}/answers/{p}').text for p in providers.PROVIDER_IDS]
    payloads.append(api.get(f'/api/v1/compare/runs/{run_id}').text)
    payloads.append(api.get('/api/v1/compare/config').text)

    for payload in payloads:
        assert DUMMY_KEY not in payload

    assert DUMMY_KEY not in json.dumps(read_answers(run_id))

    # The key did reach the provider — in the Authorization header and nowhere else.
    assert any(r['headers'].get('authorization') == f'Bearer {DUMMY_KEY}' for r in double.requests)


####################
# 12 — GET /runs/{run_id}
####################


def test_get_run_returns_current_and_latest_attempt_per_provider(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    run_id = make_run()
    api = client_as()

    double.reply_with(PROVIDER_HOST['chatgpt'], _completion('good answer'))
    api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')
    double.reply_with(PROVIDER_HOST['chatgpt'], httpx.Response(500, json={}))
    api.post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')

    double.reply_with(PROVIDER_HOST['gemini'], _completion('gemini answer'))
    api.post(f'/api/v1/compare/runs/{run_id}/answers/gemini')

    response = api.get(f'/api/v1/compare/runs/{run_id}')
    assert response.status_code == 200
    payload = response.json()

    assert payload['run']['id'] == run_id
    assert payload['run']['prompt'] == PROMPT
    # The run shape carries no admin identity.
    assert set(payload['run']) == {
        'id',
        'prompt',
        'reference',
        'status',
        'rerun_of_run_id',
        'created_at',
        # Lineage, derived in stage 6 — still no admin identity in the run object.
        'rerun_of',
        'rerun_count',
    }
    assert [p['id'] for p in payload['providers']] == list(providers.PROVIDER_IDS)
    assert [a['provider'] for a in payload['answers']] == list(providers.PROVIDER_IDS)

    by_provider = {a['provider']: a for a in payload['answers']}
    # Last good answer plus the failed retry, in one call.
    assert by_provider['chatgpt']['current']['revision'] == 1
    assert by_provider['chatgpt']['current']['text'] == 'good answer'
    assert by_provider['chatgpt']['latest_attempt']['revision'] == 2
    assert by_provider['chatgpt']['latest_attempt']['status'] == STATUS_FAILED

    assert by_provider['gemini']['current']['revision'] == 1
    assert by_provider['gemini']['latest_attempt']['revision'] == 1

    # A provider that never ran has neither.
    assert by_provider['vesqor']['current'] is None
    assert by_provider['vesqor']['latest_attempt'] is None


def test_get_run_404_for_an_unknown_run(isolated_env):
    response = client_as().get('/api/v1/compare/runs/does-not-exist')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'run_not_found'


def test_generate_404_for_unknown_run_and_400_for_unknown_provider(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()
    api = client_as()

    missing = api.post('/api/v1/compare/runs/does-not-exist/answers/chatgpt')
    assert missing.status_code == 404
    assert missing.json()['detail']['code'] == 'run_not_found'

    unknown = api.post(f'/api/v1/compare/runs/{run_id}/answers/claude')
    assert unknown.status_code == 400
    assert unknown.json()['detail']['code'] == 'unknown_provider'

    assert double.requests == []
    assert read_answers(run_id) == []


####################
# POST /runs — rerun semantics
####################


def test_rerun_creates_a_new_run_and_copies_the_prompt(isolated_env):
    api = client_as()
    first = api.post('/api/v1/compare/runs', json={'prompt': PROMPT, 'reference': REFERENCE}).json()

    rerun = api.post('/api/v1/compare/runs', json={'rerun_of_run_id': first['run']['id']})
    assert rerun.status_code == 200
    payload = rerun.json()

    assert payload['run']['id'] != first['run']['id']
    assert payload['run']['prompt'] == PROMPT
    assert payload['run']['reference'] == REFERENCE
    assert payload['run']['rerun_of_run_id'] == first['run']['id']

    # The earlier run is untouched.
    earlier = api.get(f'/api/v1/compare/runs/{first["run"]["id"]}').json()
    assert earlier['run']['prompt'] == PROMPT
    assert earlier['run']['rerun_of_run_id'] is None


def test_run_without_a_prompt_is_refused(isolated_env):
    response = client_as().post('/api/v1/compare/runs', json={'reference': REFERENCE})
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'prompt_required'


def test_rerun_of_an_unknown_run_is_404(isolated_env):
    response = client_as().post('/api/v1/compare/runs', json={'rerun_of_run_id': 'nope'})
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'run_not_found'
