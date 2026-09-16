"""VQ-25 stage 1: provider configuration, the admin gate, and the persistence layer.

Run from the repository root:  pytest test/test_vq25_answer_compare.py -q

`pytest-asyncio` is not installed and must not be installed, so the async CRUD is
driven from synchronous tests with `asyncio.run(...)`. The whole database
round-trip runs inside a single `asyncio.run` call and disposes the engine at the
end, so no pooled aiosqlite connection ever outlives the loop that created it.
The root `conftest.py` is what puts `backend/` on `sys.path` and points
`DATABASE_URL` at a throwaway SQLite file.
"""

import asyncio
import json
import re

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.answer_compare import (
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
from open_webui.utils import answer_compare_adjudication as adjudication
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils import answer_compare_tally as tally
from open_webui.utils.auth import get_current_user
from sqlalchemy import select

ALL_ENV_VARS = (
    providers.ENV_CHATGPT_BASE_URL,
    providers.ENV_CHATGPT_API_KEY,
    providers.ENV_CHATGPT_MODEL,
    providers.ENV_GEMINI_BASE_URL,
    providers.ENV_GEMINI_API_KEY,
    providers.ENV_GEMINI_MODEL,
    providers.ENV_VESQOR_BASE_URL,
    providers.ENV_VESQOR_API_KEY,
    providers.ENV_VESQOR_MODEL,
    providers.ENV_ANTHROPIC_BASE_URL,
    providers.ENV_ANTHROPIC_API_KEY,
    providers.ENV_ANTHROPIC_MODEL,
)

# Credentials belonging to other integrations. The resolver must not read any of
# them; they are cleared so an ambient value on the dev box cannot mask that, and
# set deliberately in the no-fallback tests below.
FOREIGN_CREDENTIAL_ENV_VARS = (
    'OPENAI_API_KEY',
    'OPENAI_API_BASE_URL',
    'GEMINI_API_KEY',
    'GEMINI_API_BASE_URL',
    'VESQOR_SERVICE_TOKEN',
    'VESQOR_API_BASE_URL',
)

DUMMY_KEY = 'sk-vq25-dummy-do-not-leak'
FOREIGN_KEY = 'bs_live_vq25-foreign-token-do-not-borrow'


@pytest.fixture
def clean_env(monkeypatch):
    """Every variable the resolver reads, removed — the "needs configuration" state."""
    for name in ALL_ENV_VARS + FOREIGN_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


####################
# 1 — missing-integration state
####################


def test_unconfigured_providers_name_the_exact_env_vars(clean_env):
    by_id = {p.id: p for p in providers.resolve_providers()}

    assert [p.id for p in providers.resolve_providers()] == [
        'chatgpt',
        'gemini',
        'vesqor',
        'anthropic',
    ]

    assert by_id['chatgpt'].configured is False
    assert by_id['chatgpt'].missing == [
        'ANSWER_COMPARE_CHATGPT_API_KEY',
        'ANSWER_COMPARE_CHATGPT_MODEL',
    ]
    assert by_id['chatgpt'].model is None

    assert by_id['gemini'].configured is False
    assert by_id['gemini'].missing == [
        'ANSWER_COMPARE_GEMINI_API_KEY',
        'ANSWER_COMPARE_GEMINI_MODEL',
    ]

    # Same three-variable shape as the others, and the door's URL has no default.
    assert by_id['vesqor'].configured is False
    assert by_id['vesqor'].missing == [
        'ANSWER_COMPARE_VESQOR_API_KEY',
        'ANSWER_COMPARE_VESQOR_MODEL',
        'ANSWER_COMPARE_VESQOR_BASE_URL',
    ]
    assert by_id['vesqor'].model is None
    assert by_id['vesqor'].base_url is None

    # The adjudicator: no base URL default (the gateway differs per deployment
    # and guessing one would ship the key to it), but the model IS defaulted,
    # because Sonnet 5 is the specified adjudicator rather than a guess.
    assert by_id['anthropic'].configured is False
    assert by_id['anthropic'].missing == [
        'ANSWER_COMPARE_ANTHROPIC_API_KEY',
        'ANSWER_COMPARE_ANTHROPIC_BASE_URL',
    ]
    assert by_id['anthropic'].model == 'claude-sonnet-5'
    assert by_id['anthropic'].base_url is None

    # The other two have defaults, so the page can show where it would call.
    assert by_id['chatgpt'].base_url == 'https://api.openai.com/v1'
    assert by_id['gemini'].base_url == 'https://generativelanguage.googleapis.com/v1beta/openai'


def test_partially_configured_providers_still_leak_nothing(clean_env):
    """Half-configured is the state a real box sits in: keys present, models not."""
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_GEMINI_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', 'https://door.example/api/v1')

    resolved = providers.resolve_providers()
    by_id = {p.id: p for p in resolved}

    assert by_id['chatgpt'].configured is False
    assert by_id['chatgpt'].missing == ['ANSWER_COMPARE_CHATGPT_MODEL']
    assert by_id['gemini'].configured is False
    assert by_id['gemini'].missing == ['ANSWER_COMPARE_GEMINI_MODEL']
    assert by_id['vesqor'].configured is False
    assert by_id['vesqor'].missing == ['ANSWER_COMPARE_VESQOR_MODEL']

    assert DUMMY_KEY not in json.dumps([p.model_dump() for p in resolved])


####################
# 2 — configured state, and no key material anywhere in the payload
####################


def test_configured_providers_report_no_secrets(clean_env):
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')
    clean_env.setenv('ANSWER_COMPARE_GEMINI_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_GEMINI_MODEL', 'gemini-test-1')
    # Gemini-compatible endpoints accept ?key=, so a base URL can carry a secret.
    clean_env.setenv('ANSWER_COMPARE_GEMINI_BASE_URL', f'https://gemini.example/v1?key={DUMMY_KEY}')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', f'https://user:{DUMMY_KEY}@door.example.com/api/v1')
    clean_env.setenv('ANSWER_COMPARE_ANTHROPIC_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_ANTHROPIC_BASE_URL', 'https://gateway.example/v1')

    resolved = providers.resolve_providers()
    for provider in resolved:
        assert provider.configured is True, provider.id
        assert provider.missing == [], provider.id

    by_id = {p.id: p for p in resolved}
    assert by_id['chatgpt'].model == 'gpt-test-1'
    assert by_id['gemini'].model == 'gemini-test-1'
    assert by_id['gemini'].base_url == 'https://gemini.example/v1'
    assert by_id['vesqor'].model == 'vesqor-reasoning'
    assert by_id['vesqor'].base_url == 'https://door.example.com/api/v1'
    assert by_id['anthropic'].model == 'claude-sonnet-5'
    assert by_id['anthropic'].base_url == 'https://gateway.example/v1'

    serialized = json.dumps([p.model_dump() for p in resolved])
    assert DUMMY_KEY not in serialized


####################
# 3 — no fallbacks to any other integration's credentials
####################


def test_no_fallback_to_other_integrations_credentials(clean_env):
    """Every credential belonging to another integration, set — and ignored.

    ``OPENAI_API_KEY`` may hold the VESQOR agent token on this deployment, so a
    fallback could ship it to api.openai.com; and a ``vesqor`` provider silently
    answered by OpenAI would label a ChatGPT answer VESQOR, which is a wrong
    result that looks like a right one. ``GEMINI_API_KEY`` and
    ``VESQOR_SERVICE_TOKEN`` (the billing proxy's token, a different surface)
    are ignored for the same rule, with no exceptions.
    """
    for name in FOREIGN_CREDENTIAL_ENV_VARS:
        clean_env.setenv(name, FOREIGN_KEY if 'KEY' in name or 'TOKEN' in name else 'https://foreign.example/v1')

    # Models are set, so the key is the only thing that could make these configured.
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')
    clean_env.setenv('ANSWER_COMPARE_GEMINI_MODEL', 'gemini-test-1')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')

    resolved = providers.resolve_providers()
    by_id = {p.id: p for p in resolved}

    assert by_id['chatgpt'].configured is False
    assert by_id['chatgpt'].missing == ['ANSWER_COMPARE_CHATGPT_API_KEY']
    assert by_id['gemini'].configured is False
    assert by_id['gemini'].missing == ['ANSWER_COMPARE_GEMINI_API_KEY']
    assert by_id['vesqor'].configured is False
    assert by_id['vesqor'].missing == [
        'ANSWER_COMPARE_VESQOR_API_KEY',
        'ANSWER_COMPARE_VESQOR_BASE_URL',
    ]

    # The foreign base URLs must not have been borrowed either.
    assert by_id['vesqor'].base_url is None
    assert by_id['chatgpt'].base_url == 'https://api.openai.com/v1'
    assert by_id['gemini'].base_url == 'https://generativelanguage.googleapis.com/v1beta/openai'

    assert FOREIGN_KEY not in json.dumps([p.model_dump() for p in resolved])


def test_each_provider_is_configured_by_its_own_three_variables(clean_env):
    """One shape for all three: set exactly the trio, get exactly that provider."""
    for provider_id, key_env, model_env, base_url_env in (
        (
            'chatgpt',
            'ANSWER_COMPARE_CHATGPT_API_KEY',
            'ANSWER_COMPARE_CHATGPT_MODEL',
            'ANSWER_COMPARE_CHATGPT_BASE_URL',
        ),
        ('gemini', 'ANSWER_COMPARE_GEMINI_API_KEY', 'ANSWER_COMPARE_GEMINI_MODEL', 'ANSWER_COMPARE_GEMINI_BASE_URL'),
        ('vesqor', 'ANSWER_COMPARE_VESQOR_API_KEY', 'ANSWER_COMPARE_VESQOR_MODEL', 'ANSWER_COMPARE_VESQOR_BASE_URL'),
    ):
        for name in ALL_ENV_VARS:
            clean_env.delenv(name, raising=False)
        clean_env.setenv(key_env, DUMMY_KEY)
        clean_env.setenv(model_env, 'model-test-1')
        clean_env.setenv(base_url_env, 'https://door.example/api/v1')

        resolved = providers.resolve_provider(provider_id)
        assert resolved.configured is True, provider_id
        assert resolved.missing == [], provider_id
        assert resolved.model == 'model-test-1', provider_id
        assert resolved.base_url == 'https://door.example/api/v1', provider_id

        # The other two saw none of it.
        for other in providers.PROVIDER_IDS:
            if other != provider_id:
                assert providers.resolve_provider(other).configured is False, other


####################
# Base-URL sanitising fails closed
####################


# Every one of these reached the response body or raised out of the resolver
# before the fail-closed rule: the first three smuggle credentials, the last
# three raise ValueError out of urlsplit/parts.port.
UNSAFE_BASE_URLS = (
    f'//user:{DUMMY_KEY}@host/v1',
    f'user:{DUMMY_KEY}@host/v1',
    f'{DUMMY_KEY}@host/v1',
    'http://[::1/v1',
    'http://host:notaport/v1',
    'http://host:99999/v1',
)


@pytest.mark.parametrize('raw_url', UNSAFE_BASE_URLS)
def test_unparseable_base_url_is_dropped_and_reported(clean_env, raw_url):
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_BASE_URL', raw_url)

    chatgpt = providers.resolve_provider('chatgpt')

    assert chatgpt.base_url is None
    assert chatgpt.configured is False
    assert chatgpt.missing == ['ANSWER_COMPARE_CHATGPT_BASE_URL']
    assert DUMMY_KEY not in json.dumps(chatgpt.model_dump())


@pytest.mark.parametrize('raw_url', UNSAFE_BASE_URLS)
def test_unparseable_base_url_does_not_break_the_endpoint(clean_env, raw_url):
    """One typo in one variable must stay a configuration report, not an HTTP 500."""
    clean_env.setenv('ANSWER_COMPARE_GEMINI_BASE_URL', raw_url)

    response = _client_as('admin').get('/api/v1/compare/config')

    assert response.status_code == 200
    gemini = next(p for p in response.json()['providers'] if p['id'] == 'gemini')
    assert gemini['base_url'] is None
    assert 'ANSWER_COMPARE_GEMINI_BASE_URL' in gemini['missing']
    assert DUMMY_KEY not in response.text


@pytest.mark.parametrize(
    ('raw_url', 'expected'),
    [
        (f'https://user:{DUMMY_KEY}@host/v1?key={DUMMY_KEY}', 'https://host/v1'),
        (f'https://user:{DUMMY_KEY}@host:8443/v1#frag', 'https://host:8443/v1'),
        ('https://host/v1', 'https://host/v1'),
        ('http://[::1]:8080/v1', 'http://[::1]:8080/v1'),
    ],
)
def test_parseable_base_urls_keep_only_scheme_host_port_path(raw_url, expected):
    assert providers._sanitize_base_url(raw_url) == expected


def test_blank_key_does_not_count_as_configured(clean_env):
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', '   ')
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')

    chatgpt = providers.resolve_provider('chatgpt')
    assert chatgpt.configured is False
    assert chatgpt.missing == ['ANSWER_COMPARE_CHATGPT_API_KEY']


####################
# 3b — startup configuration logging (warn-only, never fatal)
####################


def test_startup_logging_warns_on_every_missing_var_when_nothing_configured(clean_env, caplog):
    with caplog.at_level('WARNING', logger=providers.__name__):
        providers.log_startup_configuration()  # must not raise

    text = '\n'.join(record.getMessage() for record in caplog.records)
    # chatgpt/gemini have default base URLs, so only their key+model vars are
    # ever reported missing; vesqor has no default, so all three of its vars are.
    expected_missing = (
        providers.ENV_CHATGPT_API_KEY,
        providers.ENV_CHATGPT_MODEL,
        providers.ENV_GEMINI_API_KEY,
        providers.ENV_GEMINI_MODEL,
        providers.ENV_VESQOR_API_KEY,
        providers.ENV_VESQOR_MODEL,
        providers.ENV_VESQOR_BASE_URL,
        providers.ENV_ANTHROPIC_API_KEY,
        providers.ENV_ANTHROPIC_BASE_URL,
    )
    for name in expected_missing:
        assert name in text, name

    assert '0/4' in text
    # The adjudicator is called out on its own: without it the page cannot
    # adjudicate at all, which is not the same as losing one compared column.
    assert 'cannot adjudicate' in text


def test_startup_logging_reports_one_of_four_when_only_vesqor_is_configured(clean_env, caplog):
    """Mirrors production: only the VESQOR door is configured."""
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', 'https://door.example/api/v1')

    with caplog.at_level('INFO', logger=providers.__name__):
        providers.log_startup_configuration()

    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert '1/4' in text
    assert 'cannot adjudicate' in text, 'a configured candidate is not a configured adjudicator'
    # The still-unconfigured providers' missing vars are still named.
    for name in (
        providers.ENV_CHATGPT_API_KEY,
        providers.ENV_CHATGPT_MODEL,
        providers.ENV_GEMINI_API_KEY,
        providers.ENV_GEMINI_MODEL,
    ):
        assert name in text, name


def test_startup_logging_reports_four_of_four_when_all_configured(clean_env, caplog):
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')
    clean_env.setenv('ANSWER_COMPARE_GEMINI_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_GEMINI_MODEL', 'gemini-test-1')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', 'https://door.example/api/v1')
    clean_env.setenv('ANSWER_COMPARE_ANTHROPIC_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_ANTHROPIC_BASE_URL', 'https://gateway.example/v1')

    with caplog.at_level('INFO', logger=providers.__name__):
        providers.log_startup_configuration()  # must not raise

    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert '4/4' in text
    assert 'cannot adjudicate' not in text


def test_startup_logging_never_leaks_a_configured_providers_key(clean_env, caplog):
    """A distinctive sentinel key must not appear in any emitted record, even partially."""
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', f'https://user:{DUMMY_KEY}@door.example.com/api/v1')

    with caplog.at_level('INFO', logger=providers.__name__):
        providers.log_startup_configuration()

    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert DUMMY_KEY not in text
    # The base URL must have been sanitised in the log line too, not just the field.
    assert 'door.example.com' in text
    assert 'user:' not in text


####################
# 4 — admin-only
####################


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


def _client_as(role: str) -> TestClient:
    """A bare app carrying only this router, with `get_current_user` replaced.

    Only `get_current_user` is overridden: the real `get_admin_user` stays in the
    chain, so the role check itself is what the test exercises.
    """
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def test_config_endpoint_allows_an_admin(clean_env):
    response = _client_as('admin').get('/api/v1/compare/config')

    assert response.status_code == 200
    payload = response.json()
    assert [p['id'] for p in payload['providers']] == ['chatgpt', 'gemini', 'vesqor', 'anthropic']
    assert payload['providers'][0]['configured'] is False
    assert set(payload['providers'][0]) == {'id', 'configured', 'missing', 'base_url', 'model', 'can_judge'}


def test_config_endpoint_rejects_a_non_admin(clean_env):
    response = _client_as('user').get('/api/v1/compare/config')

    assert response.status_code == 401
    # The message proves the refusal came from the role check in get_admin_user,
    # not from a token problem somewhere earlier in the chain.
    assert response.json()['detail'] == ERROR_MESSAGES.ACCESS_PROHIBITED


####################
# 5 — model round-trip, including verdict history
####################


async def _stored_rows(model, run_id: str) -> list[dict]:
    """Read a table directly, ordered by revision.

    Answers and summaries have no history getter in the CRUD (stage 1 does not
    need one), but the append-only rule is about what is *stored*, so the test
    asserts against the rows rather than against an API that could hide an
    overwrite behind a max(revision) query.
    """
    from open_webui.internal.db import get_async_db_context

    async with get_async_db_context() as db:
        result = await db.execute(select(model).where(model.run_id == run_id).order_by(model.revision))
        return [
            {
                'id': row.id,
                'revision': row.revision,
                # 'text' on answers, 'narrative' on summaries — the payload that
                # an overwrite would have replaced.
                'body': getattr(row, 'text', None) if hasattr(row, 'text') else row.narrative,
            }
            for row in result.scalars().all()
        ]


async def _round_trip() -> dict:
    from open_webui.internal.db import Base, async_engine

    tables = [
        AnswerCompareRun.__table__,
        AnswerCompareAnswer.__table__,
        AnswerCompareReport.__table__,
        AnswerCompareSummary.__table__,
    ]
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email='admin@example.com',
            form=AnswerCompareRunForm(prompt='Compare these answers.', reference='Some reference.'),
        )

        first_answer = await AnswerCompareAnswers.insert_next_revision(
            run_id=run.id,
            provider=providers.PROVIDER_VESQOR,
            status='complete',
            requested_model='vesqor-engine',
            text='first answer',
        )
        second_answer = await AnswerCompareAnswers.insert_next_revision(
            run_id=run.id,
            provider=providers.PROVIDER_VESQOR,
            status='complete',
            requested_model='vesqor-engine',
            text='regenerated answer',
        )

        first_report = await AnswerCompareReports.insert_next_revision(
            run_id=run.id,
            judge=providers.PROVIDER_CHATGPT,
            status='complete',
            label_map={'A': 'vesqor'},
            report={'winner': 'A'},
            judged_versions=[{'provider': 'vesqor', 'revision': 1}],
        )
        second_report = await AnswerCompareReports.insert_next_revision(
            run_id=run.id,
            judge=providers.PROVIDER_CHATGPT,
            status='complete',
            label_map={'A': 'vesqor'},
            report={'winner': 'A', 're_judged': True},
            judged_versions=[{'provider': 'vesqor', 'revision': 2}],
        )

        first_summary = await AnswerCompareSummaries.insert_next_revision(
            run_id=run.id,
            narrative='first summary',
            tally={'vesqor': 1},
            judged_versions=[{'provider': 'vesqor', 'revision': 1}],
            partial=True,
            included_judges=['chatgpt'],
        )
        second_summary = await AnswerCompareSummaries.insert_next_revision(
            run_id=run.id,
            narrative='recomputed summary',
            tally={'vesqor': 1},
            judged_versions=[{'provider': 'vesqor', 'revision': 2}],
            partial=False,
            included_judges=['chatgpt'],
        )

        return {
            'run': await AnswerCompareRuns.get_by_id(run.id),
            'runs': await AnswerCompareRuns.list_runs(limit=10),
            'answer_revisions': (first_answer.revision, second_answer.revision),
            'first_answer_id': first_answer.id,
            'current_answers': await AnswerCompareAnswers.get_current_by_run(run.id),
            'current_versions': await AnswerCompareAnswers.get_current_versions(run.id),
            'stored_answers': await _stored_rows(AnswerCompareAnswer, run.id),
            'report_revisions': (first_report.revision, second_report.revision),
            'first_report_id': first_report.id,
            'report_history': await AnswerCompareReports.get_all_by_run(run.id),
            'current_reports': await AnswerCompareReports.get_current_by_run(run.id),
            'summary_revisions': (first_summary.revision, second_summary.revision),
            'first_summary_id': first_summary.id,
            'current_summary': await AnswerCompareSummaries.get_current_by_run(run.id),
            'stored_summaries': await _stored_rows(AnswerCompareSummary, run.id),
        }
    finally:
        await async_engine.dispose()


def test_model_round_trip_keeps_every_revision():
    result = asyncio.run(_round_trip())

    run = result['run']
    assert run is not None
    assert run.status == 'active'
    assert run.prompt == 'Compare these answers.'
    assert run.admin_email == 'admin@example.com'
    assert run.id in [r.id for r in result['runs']]

    # Answers are append-only: a regeneration inserts revision 2 and the stored
    # revision 1 row — same id, same body — must still be there afterwards.
    assert result['answer_revisions'] == (1, 2)
    stored_answers = result['stored_answers']
    assert [row['revision'] for row in stored_answers] == [1, 2]
    assert stored_answers[0]['id'] == result['first_answer_id']
    assert stored_answers[0]['body'] == 'first answer'
    assert stored_answers[1]['body'] == 'regenerated answer'

    current_answers = result['current_answers']
    assert len(current_answers) == 1
    assert current_answers[0].revision == 2
    assert current_answers[0].text == 'regenerated answer'
    assert [(v.provider, v.revision) for v in result['current_versions']] == [('vesqor', 2)]

    # Reports are append-only: re-judging must not have replaced revision 1.
    assert result['report_revisions'] == (1, 2)
    history = result['report_history']
    assert [r.revision for r in history] == [1, 2]
    assert result['first_report_id'] in [r.id for r in history]
    superseded = next(r for r in history if r.revision == 1)
    assert superseded.judged_versions == [{'provider': 'vesqor', 'revision': 1}]
    assert superseded.report == {'winner': 'A'}

    current_reports = result['current_reports']
    assert len(current_reports) == 1
    assert current_reports[0].revision == 2

    # The superseded report judged revision 1 while the run is now at revision 2 —
    # which is exactly how "outdated" is derived, without storing a flag.
    current_versions = [{'provider': v.provider, 'revision': v.revision} for v in result['current_versions']]
    assert superseded.judged_versions != current_versions
    assert current_reports[0].judged_versions == current_versions

    # Summaries are append-only too; recomputing keeps the superseded row.
    assert result['summary_revisions'] == (1, 2)
    stored_summaries = result['stored_summaries']
    assert [row['revision'] for row in stored_summaries] == [1, 2]
    assert stored_summaries[0]['id'] == result['first_summary_id']
    assert stored_summaries[0]['body'] == 'first summary'
    assert stored_summaries[1]['body'] == 'recomputed summary'

    assert result['current_summary'].revision == 2
    assert result['current_summary'].narrative == 'recomputed summary'
    assert result['current_summary'].partial is False


####################
# 6 — VQ-25: the compared systems are subjects, never judges
####################


@pytest.fixture
def clean_can_judge_env(monkeypatch):
    """Kept as a fixture so the tests below read the same as they did.

    There is nothing left to clean: judge capability used to be a per-provider
    environment variable and is now structural. The fixture asserts that — a
    stray ``ANSWER_COMPARE_*_CAN_JUDGE`` in an environment must be inert, not
    quietly hand a candidate the judge role back.
    """
    for provider_id in providers.PROVIDER_IDS:
        monkeypatch.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_CAN_JUDGE', 'true')
    return monkeypatch


def test_no_candidate_can_judge_whatever_the_environment_says(clean_can_judge_env):
    """The three compared systems never judge — including their own answers."""
    for provider_id in providers.GENERATOR_IDS:
        assert providers.resolve_can_judge(provider_id) is False, provider_id


def test_the_adjudicator_judges_and_is_not_one_of_the_candidates(clean_can_judge_env):
    assert providers.resolve_can_judge(providers.ADJUDICATOR_ID) is True
    assert providers.ADJUDICATOR_ID not in providers.GENERATOR_IDS


def test_the_two_roles_are_disjoint():
    """The property the whole split exists for, asserted directly."""
    assert set(providers.GENERATOR_IDS) & set(providers.JUDGE_IDS) == set()
    assert providers.PROVIDER_IDS == providers.GENERATOR_IDS + providers.JUDGE_IDS


def test_resolve_can_judge_rejects_an_unknown_provider():
    with pytest.raises(ValueError):
        providers.resolve_can_judge('not-a-provider')


def test_resolve_judge_ids_returns_the_single_adjudicator(clean_can_judge_env):
    assert providers.resolve_judge_ids() == ('anthropic',)


def test_judge_capability_is_not_configurable_at_all(clean_can_judge_env):
    """No environment variable can move the judge role, in either direction."""
    clean_can_judge_env.setenv('ANSWER_COMPARE_ANTHROPIC_CAN_JUDGE', 'false')
    clean_can_judge_env.setenv('ANSWER_COMPARE_VESQOR_CAN_JUDGE', 'true')
    assert providers.resolve_judge_ids() == ('anthropic',)
    assert not hasattr(providers, 'can_judge_env'), 'the CAN_JUDGE seam must stay gone'


def test_provider_config_carries_can_judge(clean_env, clean_can_judge_env):
    by_id = {p.id: p for p in providers.resolve_providers()}
    assert by_id['chatgpt'].can_judge is False
    assert by_id['gemini'].can_judge is False
    assert by_id['vesqor'].can_judge is False
    assert by_id['anthropic'].can_judge is True


def test_config_endpoint_exposes_can_judge_per_provider(clean_env, clean_can_judge_env):
    response = _client_as('admin').get('/api/v1/compare/config')

    assert response.status_code == 200
    by_id = {p['id']: p for p in response.json()['providers']}
    assert by_id['chatgpt']['can_judge'] is False
    assert by_id['gemini']['can_judge'] is False
    assert by_id['vesqor']['can_judge'] is False
    assert by_id['anthropic']['can_judge'] is True


def test_the_config_endpoint_never_returns_adjudicator_key_material(clean_env, monkeypatch):
    """The adjudicator's key is reached through one header and is never reported."""
    monkeypatch.setenv('ANSWER_COMPARE_ANTHROPIC_API_KEY', 'sk-secret-adjudicator-key')
    monkeypatch.setenv('ANSWER_COMPARE_ANTHROPIC_BASE_URL', 'https://gateway.example/v1')

    response = _client_as('admin').get('/api/v1/compare/config')

    assert response.status_code == 200
    assert 'sk-secret-adjudicator-key' not in response.text


def test_the_adjudicator_defaults_to_the_specified_model(clean_env):
    """Sonnet 5 is the specified adjudicator, so it is a default, not a guess."""
    config = providers.resolve_provider('anthropic')
    assert config.model == 'claude-sonnet-5'
    assert providers.ENV_ANTHROPIC_MODEL not in config.missing


def test_the_adjudicator_base_url_has_no_default(clean_env):
    """Guessing a gateway URL would ship the key and every answer to it."""
    config = providers.resolve_provider('anthropic')
    assert config.base_url is None
    assert providers.ENV_ANTHROPIC_BASE_URL in config.missing


def test_no_generator_gets_an_invented_model_default(clean_env):
    for provider_id in providers.GENERATOR_IDS:
        config = providers.resolve_provider(provider_id)
        assert config.model is None, provider_id


####################
# 6a — tally: the judge panel is the adjudicator, not every provider
####################

LABEL_MAP_2 = {'A': 'chatgpt', 'B': 'gemini'}


def _raw_report_2(kind: str, providers_named: list[str]) -> dict:
    label_of = {p: label for label, p in LABEL_MAP_2.items()}
    return {
        'answers': [],
        'verdict': {'kind': kind, 'labels': [label_of[p] for p in providers_named]},
        'rationale': 'because',
        'needs_verification': [],
    }


def _tally_report_2(kind: str, providers_named: list[str], versions: list[dict]) -> tally.TallyReport:
    return tally.TallyReport(
        revision=1,
        judged_versions=versions,
        label_map=dict(LABEL_MAP_2),
        report=_raw_report_2(kind, providers_named),
    )


_VERSIONS_2 = [{'provider': 'chatgpt', 'revision': 1}, {'provider': 'gemini', 'revision': 1}]


def test_tally_the_single_adjudicator_is_the_whole_panel(clean_can_judge_env):
    """One fresh verdict IS the panel's verdict — it must not read as partial."""
    judges = [tally.TallyJudge(judge='anthropic', current=_tally_report_2('winner', ['chatgpt'], _VERSIONS_2))]

    result = tally.compute_tally(_VERSIONS_2, judges)

    assert result['n_included'] == 1
    assert result['partial'] is False
    assert result['included_judges'] == ['anthropic']


def test_tally_a_single_adjudicators_verdict_is_the_preferred_answer(clean_can_judge_env):
    """DECISIONS.md#014 guards a PARTIAL panel, not a panel that is one by design.

    Requiring two included verdicts on a one-judge panel would report "no
    majority" for every adjudication the product can ever produce, which is
    false rather than cautious.
    """
    judges = [tally.TallyJudge(judge='anthropic', current=_tally_report_2('winner', ['chatgpt'], _VERSIONS_2))]

    result = tally.compute_tally(_VERSIONS_2, judges)

    assert result['outcome'] == {'kind': 'preferred', 'provider': 'chatgpt'}


def test_tally_one_of_a_larger_panel_is_still_not_preferred(monkeypatch):
    """The same code, on a three-judge panel: one voter is still one opinion."""
    monkeypatch.setattr(tally, 'resolve_judge_ids', lambda: ('anthropic', 'second', 'third'))
    judges = [tally.TallyJudge(judge='anthropic', current=_tally_report_2('winner', ['chatgpt'], _VERSIONS_2))]

    result = tally.compute_tally(_VERSIONS_2, judges)

    assert result['n_included'] == 1
    assert result['partial'] is True
    assert result['outcome'] == {'kind': 'no_majority', 'provider': None}


@pytest.mark.parametrize('candidate', ['chatgpt', 'gemini', 'vesqor'])
def test_tally_ignores_a_judge_entry_for_a_candidate(clean_can_judge_env, candidate):
    """Even handed in by mistake, a compared system is never tallied as a judge."""
    judges = [
        tally.TallyJudge(judge='anthropic', current=_tally_report_2('winner', ['chatgpt'], _VERSIONS_2)),
        tally.TallyJudge(judge=candidate, configured=True, current=None),
    ]

    result = tally.compute_tally(_VERSIONS_2, judges)

    assert result['n_included'] == 1
    assert result['partial'] is False
    assert all(item['judge'] != candidate for item in result['excluded'])
    assert all(entry['judge'] != candidate for entry in result['verdicts'])


def test_tally_the_adjudicator_missing_its_report_is_correctly_partial(clean_can_judge_env):
    """With the panel unreported there is no verdict at all — and it says so."""
    judges = [tally.TallyJudge(judge='anthropic', current=None)]

    result = tally.compute_tally(_VERSIONS_2, judges)

    assert result['n_included'] == 0
    assert result['partial'] is True
    assert result['excluded'] == [{'judge': 'anthropic', 'reason': 'no_report'}]
    assert result['outcome'] == {'kind': 'no_valid_verdicts', 'provider': None}


####################
# 6b — router: the judge endpoint and run-all honour judge capability
####################


async def _create_compare_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    tables = [AnswerCompareRun.__table__, AnswerCompareAnswer.__table__, AnswerCompareReport.__table__]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


def _make_run() -> str:
    async def _run():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email='admin@example.com',
            form=AnswerCompareRunForm(prompt='Compare these answers.', reference='Some reference.'),
        )
        return run.id

    return asyncio.run(_run())


def _seed_complete_answers(run_id: str, provider_ids: tuple[str, ...]) -> None:
    async def _seed():
        for provider_id in provider_ids:
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run_id,
                provider=provider_id,
                status='complete',
                requested_model=f'{provider_id}-model-test-1',
                model=f'{provider_id}-model-test-1-resolved',
                text=f'answer from {provider_id}',
            )

    asyncio.run(_seed())


def _report_rows(run_id: str) -> list:
    return asyncio.run(AnswerCompareReports.get_all_by_run(run_id))


# The canonical engine's candidate block. The double has to read the prompt the
# engine really writes: a stale pattern here yields zero labels and the double
# starts answering about candidates that were never asked about.
CANDIDATE_BLOCK_RE = re.compile(r'=== CANDIDATE ([A-Z]) ===\n(.*?)\n=== END CANDIDATE \1 ===', re.S)


def _labels_in(body: dict) -> list[str]:
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    labels = [m.group(1) for m in CANDIDATE_BLOCK_RE.finditer(user)]
    assert labels, 'the double could not find a candidate block — the prompt format moved'
    return labels


def _candidate_findings(label: str, winner: bool) -> dict:
    """One candidate's findings in the canonical schema.

    Only findings and per-category points: the double never writes a total, a
    ranking or a winner's score, because the engine computes all of those from
    exactly this and would silently accept a model that made them up.
    """
    fraction = 0.9 if winner else 0.6
    return {
        'label': label,
        'claims': [
            {
                'claim': 'the deploy succeeded',
                'passage': 'the deploy succeeded',
                # NOT_VERIFIABLE, not VERIFIED: the runs in this file carry no
                # reference corpus, and the engine rightly refuses a VERIFIED
                # material claim that cites no evidence.
                'classification': adjudication.CLAIM_NOT_VERIFIABLE,
                'material': True,
                'evidence_ids': [],
                'note': '',
            }
        ],
        'category_scores': {
            key: round(adjudication.CATEGORY_WEIGHTS[key] * fraction, 1) for key in adjudication.CATEGORY_KEYS
        },
        'penalties': [],
        'coverage': {'covered_evidence_ids': [], 'missed_evidence_ids': []},
        'strengths': [{'passage': 'a passage', 'note': 'clear'}],
        'critical_errors': [],
        'critical_omissions': [],
        'improvements': [{'passage': 'a passage', 'note': 'be specific'}],
        'unnecessary': [],
        'useful_extras': [],
    }


def _valid_report_for(labels: list[str]) -> dict:
    return {
        'evidence_matrix': [],
        'candidates': [_candidate_findings(label, label == labels[0]) for label in labels],
        'category_winners': {key: [labels[0]] for key in adjudication.CATEGORY_KEYS},
        'pairwise': [],
        'declared_winner': labels[0],
        'confidence': adjudication.CONFIDENCE_MEDIUM,
        'decisive_reasons': ['Better supported by the reference.'],
        'winner_gap_analysis': ['Still hedges on the timeline.'],
        'loser_recovery_analysis': [{'label': label, 'actions': ['cite the log']} for label in labels[1:]],
        'final_adjudication': 'Answer is fine.',
        'unresolved_uncertainty': [],
        'needs_verification': [],
    }


class _JudgeDouble:
    """Records every request the router sent out; replies with a valid report."""

    def __init__(self):
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.requests.append({'url': str(request.url), 'body': body})
        content = json.dumps(_valid_report_for(_labels_in(body)))
        return httpx.Response(200, json={'model': 'judge-model-test-1', 'choices': [{'message': {'content': content}}]})


def _install_judge_double(monkeypatch) -> _JudgeDouble:
    double = _JudgeDouble()

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout)

    monkeypatch.setattr(provider_client, '_build_async_client', build)
    return double


def _configure_provider(monkeypatch, provider_id: str) -> None:
    monkeypatch.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_BASE_URL', f'https://{provider_id}.example/v1')
    monkeypatch.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_API_KEY', DUMMY_KEY)
    monkeypatch.setenv(f'ANSWER_COMPARE_{provider_id.upper()}_MODEL', f'{provider_id}-model-test-1')


@pytest.mark.parametrize('candidate', ['chatgpt', 'gemini', 'vesqor'])
def test_judge_endpoint_refuses_every_candidate_and_writes_nothing(
    clean_env, clean_can_judge_env, monkeypatch, candidate
):
    """No compared system can be asked to judge — not even a fully configured one."""
    asyncio.run(_create_compare_tables())
    _configure_provider(monkeypatch, candidate)
    run_id = _make_run()

    response = _client_as('admin').post(f'/api/v1/compare/runs/{run_id}/reports/{candidate}')

    assert response.status_code == 400
    assert response.json()['detail'] == {'code': 'judge_not_capable', 'judge': candidate}
    assert _report_rows(run_id) == []


def test_judge_endpoint_accepts_the_adjudicator(clean_env, clean_can_judge_env, monkeypatch):
    asyncio.run(_create_compare_tables())
    _configure_provider(monkeypatch, 'chatgpt')
    _configure_provider(monkeypatch, 'gemini')
    _configure_provider(monkeypatch, 'anthropic')
    _install_judge_double(monkeypatch)

    run_id = _make_run()
    _seed_complete_answers(run_id, ('chatgpt', 'gemini'))

    response = _client_as('admin').post(f'/api/v1/compare/runs/{run_id}/reports/anthropic')

    assert response.status_code == 200
    assert response.json()['status'] == 'complete', response.json().get('error')
    rows = _report_rows(run_id)
    assert [r.judge for r in rows] == ['anthropic']


def test_the_adjudicator_cannot_be_asked_to_generate_an_answer(clean_env, monkeypatch):
    """The independence the split exists for, enforced at the endpoint."""
    asyncio.run(_create_compare_tables())
    _configure_provider(monkeypatch, 'anthropic')
    run_id = _make_run()

    response = _client_as('admin').post(f'/api/v1/compare/runs/{run_id}/answers/anthropic')

    assert response.status_code == 400
    assert response.json()['detail'] == {'code': 'unknown_provider', 'provider': 'anthropic'}


def test_run_all_calls_the_adjudicator_only_however_many_candidates_are_configured(
    clean_env, clean_can_judge_env, monkeypatch
):
    asyncio.run(_create_compare_tables())
    # Every candidate fully configured — the point is that being configured
    # does not make one of them a judge.
    _configure_provider(monkeypatch, 'chatgpt')
    _configure_provider(monkeypatch, 'gemini')
    _configure_provider(monkeypatch, 'vesqor')
    _configure_provider(monkeypatch, 'anthropic')
    double = _install_judge_double(monkeypatch)

    run_id = _make_run()
    _seed_complete_answers(run_id, ('chatgpt', 'gemini', 'vesqor'))

    response = _client_as('admin').post(f'/api/v1/compare/runs/{run_id}/reports')

    assert response.status_code == 200
    payload = response.json()
    by_judge = {entry['judge']: entry for entry in payload['results']}
    # No candidate appears at all — not complete, not failed, not even skipped.
    assert set(by_judge) == {'anthropic'}
    assert by_judge['anthropic']['status'] == 'complete'

    rows = _report_rows(run_id)
    assert sorted(r.judge for r in rows) == ['anthropic']

    # Exactly one request went out: three candidates' doors were never called.
    assert len(double.requests) == 1
