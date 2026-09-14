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
from open_webui.utils import answer_compare_providers as providers
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

    assert [p.id for p in providers.resolve_providers()] == ['chatgpt', 'gemini', 'vesqor']

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
    )
    for name in expected_missing:
        assert name in text, name

    assert '0/3' in text


def test_startup_logging_reports_one_of_three_when_only_vesqor_is_configured(clean_env, caplog):
    """Mirrors production: only the VESQOR door is configured."""
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', 'https://door.example/api/v1')

    with caplog.at_level('INFO', logger=providers.__name__):
        providers.log_startup_configuration()

    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert '1/3' in text
    # The still-unconfigured providers' missing vars are still named.
    for name in (
        providers.ENV_CHATGPT_API_KEY,
        providers.ENV_CHATGPT_MODEL,
        providers.ENV_GEMINI_API_KEY,
        providers.ENV_GEMINI_MODEL,
    ):
        assert name in text, name


def test_startup_logging_reports_three_of_three_when_all_configured(clean_env, caplog):
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_CHATGPT_MODEL', 'gpt-test-1')
    clean_env.setenv('ANSWER_COMPARE_GEMINI_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_GEMINI_MODEL', 'gemini-test-1')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_API_KEY', DUMMY_KEY)
    clean_env.setenv('ANSWER_COMPARE_VESQOR_MODEL', 'vesqor-reasoning')
    clean_env.setenv('ANSWER_COMPARE_VESQOR_BASE_URL', 'https://door.example/api/v1')

    with caplog.at_level('INFO', logger=providers.__name__):
        providers.log_startup_configuration()  # must not raise

    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert '3/3' in text


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
    assert [p['id'] for p in payload['providers']] == ['chatgpt', 'gemini', 'vesqor']
    assert payload['providers'][0]['configured'] is False
    assert set(payload['providers'][0]) == {'id', 'configured', 'missing', 'base_url', 'model'}


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
