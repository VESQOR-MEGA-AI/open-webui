"""VQ-25: the acceptance matrix — one test per line of the ticket's acceptance list.

Run from the repository root:  pytest test/test_vq25_acceptance.py -q

Each test asserts *behaviour* by calling the real code paths, so a requirement
that breaks fails here rather than merely being documented. Where an earlier
stage already covers a requirement in depth, the test here exercises the same
path in miniature — it is a gate, not a duplicate of those suites.

**Why the list is a constant and not read from the ticket:** `SPEC.md` lives in
`/opt/work-archive/`, outside the repository. A test reading it would raise
FileNotFoundError on any checkout — dead exactly when it is needed. So the 16
requirement strings are copied here verbatim, and reconciling this copy with the
ticket is a human step on the stage-7 checklist.

Source: VQ-25 ticket, SPEC.md → "Acceptance Criteria", first bullet.
Copied: 2026-09-13.
"""

import asyncio
import json
import secrets
from typing import Any, Optional

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
    AnswerCompareSummary,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils import answer_compare_tally as tally
from open_webui.utils.auth import get_current_user
from sqlalchemy import delete, select

# --- The ticket's acceptance list, verbatim (see the module docstring) ---------
ACCEPTANCE_REQUIREMENTS = (
    'all three answers generating',
    'partial provider failure',
    'each judge alone',
    'all three judges with no cross-visibility',
    'two-answer judging',
    'correct mapping of anonymous labels',
    'self-vote flags',
    'malformed reports excluded from the tally',
    'tallies for ties',
    'no reliable winner',
    'partial summaries',
    'outdated marking after regeneration with earlier runs unchanged',
    'reopen and rerun',
    'oversized input',
    'missing-integration state',
    'rejection of non-admin requests',
)


####################
# The legacy three-judge panel
####################

# The adjudication panel in the product is ONE independent adjudicator. The
# tally, the summary and the run-all endpoint are nevertheless written to a
# panel of unknown size — nothing in them may index `[0]` or assume a length —
# so the mechanics they implement (strict majority, exclusion, self-votes,
# freshness, per-judge isolation) still need a multi-judge panel to be
# exercised at all. Pinning one here keeps these tests testing those mechanics,
# and is itself the standing proof that no caller assumes a panel of one. Panel
# *composition* is asserted in test_vq25_answer_compare.py, where it belongs.
_PANEL_SEAMS = (
    providers,
    tally,
    answer_compare_router,
)

LEGACY_PANEL = ('chatgpt', 'gemini', 'vesqor')


@pytest.fixture(autouse=True)
def legacy_judge_panel(monkeypatch):
    for module in _PANEL_SEAMS:
        monkeypatch.setattr(module, 'resolve_judge_ids', lambda: LEGACY_PANEL)
    return monkeypatch


PROVIDERS = list(providers.GENERATOR_IDS)
DUMMY_KEY = 'sk-vq25-acceptance-secret'
PROVIDER_ENV = {
    p: (
        f'ANSWER_COMPARE_{p.upper()}_BASE_URL',
        f'ANSWER_COMPARE_{p.upper()}_API_KEY',
        f'ANSWER_COMPARE_{p.upper()}_MODEL',
    )
    for p in PROVIDERS
}
HOST = {p: f'https://{p}.example/v1' for p in PROVIDERS}
ANSWER = {p: f'{p} says something about latency' for p in PROVIDERS}


####################
# Harness
####################


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for names in PROVIDER_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for p in PROVIDERS:
        monkeypatch.delenv(providers.max_input_chars_env(p), raising=False)
        monkeypatch.delenv(providers.judge_max_input_chars_env(p), raising=False)
    judge.reset_mode_cache()
    asyncio.run(_create_tables())
    asyncio.run(_clear_tables())
    return monkeypatch


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    models = (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[m.__table__ for m in models]))


async def _clear_tables() -> None:
    from open_webui.internal.db import get_async_db_context

    async with get_async_db_context() as db:
        for model in (AnswerCompareSummary, AnswerCompareReport, AnswerCompareAnswer, AnswerCompareRun):
            await db.execute(delete(model))
        await db.commit()


def configure(monkeypatch, *ids: str) -> None:
    for p in ids:
        base, key, model = PROVIDER_ENV[p]
        monkeypatch.setenv(base, HOST[p])
        monkeypatch.setenv(key, DUMMY_KEY)
        monkeypatch.setenv(model, f'{p}-model')


def labels_in(body: dict) -> list[str]:
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    return [line.split()[2] for line in user.splitlines() if line.startswith('=== ANSWER ')]


def answers_in(body: dict) -> dict[str, str]:
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    out, label = {}, None
    for line in user.splitlines():
        if line.startswith('=== ANSWER '):
            label = line.split()[2]
            out[label] = []
        elif line.startswith('=== END ANSWER '):
            out[label] = '\n'.join(out[label])
            label = None
        elif label is not None:
            out[label].append(line)
    return out


def report_for(labels: list[str], kind: str = 'winner', named: Optional[list[str]] = None) -> dict:
    return {
        'answers': [
            {
                'label': label,
                'strengths': [],
                'errors_or_unsupported': [],
                'omissions': [],
                'useful_extras': [],
                'unnecessary': [],
                'improvements': [],
            }
            for label in labels
        ],
        'verdict': {'kind': kind, 'labels': named if named is not None else [labels[0]]},
        'rationale': 'because',
        'needs_verification': [],
    }


def completion(content: Any) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={'model': 'm', 'choices': [{'message': {'content': text}}]})


class Double:
    def __init__(self):
        self.requests: list[dict] = []
        self.responders: dict[str, Any] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.requests.append({'url': str(request.url), 'body': body})
        for host, responder in self.responders.items():
            if str(request.url).startswith(host):
                return responder(body)
        return completion(
            report_for(labels_in(body)) if 'ANSWERS TO EVALUATE' in json.dumps(body) else 'a generated answer'
        )

    def sent_to(self, provider: str) -> list[dict]:
        return [r for r in self.requests if r['url'].startswith(HOST[provider])]


def install(monkeypatch) -> Double:
    double = Double()
    monkeypatch.setattr(
        provider_client,
        '_build_async_client',
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout),
    )
    return double


def _user(role: str) -> UserModel:
    return UserModel(
        id=f'{role}-id', email=f'{role}@example.com', name=role, role=role, last_active_at=0, updated_at=0, created_at=0
    )


def client_as(role: str = 'admin') -> TestClient:
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def create_run(prompt: str = 'Which is faster?', reference: Optional[str] = None) -> str:
    return client_as().post('/api/v1/compare/runs', json={'prompt': prompt, 'reference': reference}).json()['run']['id']


def generate(run_id: str, provider: str) -> dict:
    return client_as().post(f'/api/v1/compare/runs/{run_id}/answers/{provider}').json()


def seed_answers(run_id: str, *ids: str) -> None:
    async def _seed():
        for p in ids or PROVIDERS:
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run_id, provider=p, status=STATUS_COMPLETE, text=ANSWER[p]
            )

    asyncio.run(_seed())


def judge_run(run_id: str, judge_id: str) -> dict:
    return client_as().post(f'/api/v1/compare/runs/{run_id}/reports/{judge_id}').json()


def get_run(run_id: str) -> dict:
    return client_as().get(f'/api/v1/compare/runs/{run_id}').json()


####################
# 1–16, one test per requirement
####################


def test_all_three_answers_generating(env):
    configure(env, *PROVIDERS)
    double = install(env)
    run_id = create_run()

    for p in PROVIDERS:
        assert generate(run_id, p)['status'] == STATUS_COMPLETE

    answers = {a['provider']: a for a in get_run(run_id)['answers']}
    assert all(answers[p]['current']['text'] for p in PROVIDERS)
    assert all(double.sent_to(p) for p in PROVIDERS)


def test_partial_provider_failure(env):
    configure(env, *PROVIDERS)
    double = install(env)
    double.responders[HOST['gemini']] = lambda body: httpx.Response(500, json={})
    run_id = create_run()

    for p in PROVIDERS:
        generate(run_id, p)

    answers = {a['provider']: a for a in get_run(run_id)['answers']}
    assert answers['gemini']['latest_attempt']['status'] == STATUS_FAILED
    # The other two are untouched — the whole point of the requirement.
    assert answers['chatgpt']['current']['text'] and answers['vesqor']['current']['text']


def test_each_judge_alone(env):
    configure(env, *PROVIDERS)
    install(env)
    run_id = create_run()
    seed_answers(run_id)

    for judge_id in PROVIDERS:
        report = judge_run(run_id, judge_id)
        assert report['status'] == STATUS_COMPLETE, judge_id
        assert report['judge'] == judge_id
        assert report['mapped']['verdict']['providers']


def test_all_three_judges_with_no_cross_visibility(env):
    configure(env, *PROVIDERS)
    double = install(env)
    markers = {p: secrets.token_hex(8) for p in PROVIDERS}
    for p in PROVIDERS:
        double.responders[HOST[p]] = lambda body, m=markers[p]: completion(
            {**report_for(labels_in(body)), 'rationale': f'secret {m}'}
        )
    run_id = create_run()
    seed_answers(run_id)

    results = client_as().post(f'/api/v1/compare/runs/{run_id}/reports').json()['results']
    assert [r['status'] for r in results] == ['complete'] * 3

    for p in PROVIDERS:
        sent = json.dumps(double.sent_to(p))
        for other, marker in markers.items():
            if other != p:
                assert marker not in sent, f'{p} saw {other}'


def test_two_answer_judging(env):
    configure(env, *PROVIDERS)
    double = install(env)
    run_id = create_run()
    seed_answers(run_id, 'chatgpt', 'gemini')

    report = judge_run(run_id, 'vesqor')
    assert report['status'] == STATUS_COMPLETE
    assert labels_in(double.sent_to('vesqor')[0]['body']) == ['A', 'B']
    assert report['missing_providers'] == ['vesqor']


def test_correct_mapping_of_anonymous_labels(env):
    configure(env, *PROVIDERS)
    double = install(env)
    run_id = create_run()
    seed_answers(run_id)

    report = judge_run(run_id, 'chatgpt')
    label_map = report['label_map']
    received = answers_in(double.sent_to('chatgpt')[0]['body'])

    # The invariant: the text under a label IS that provider's answer.
    for label, text in received.items():
        assert text == ANSWER[label_map[label]], label
    winner_label = report['report']['verdict']['labels'][0]
    assert report['mapped']['verdict']['providers'] == [label_map[winner_label]]


def test_self_vote_flags(env):
    configure(env, *PROVIDERS)
    double = install(env)
    run_id = create_run()
    seed_answers(run_id)

    # Each judge names its own answer the winner.
    for p in PROVIDERS:
        double.responders[HOST[p]] = lambda body, me=p: completion(
            report_for(
                labels_in(body),
                named=[next(label for label, text in answers_in(body).items() if text == ANSWER[me])],
            )
        )
    client_as().post(f'/api/v1/compare/runs/{run_id}/reports')

    result = get_run(run_id)['tally']
    assert sorted(item['judge'] for item in result['self_votes']) == PROVIDERS
    # Annotation only: every vote still counted.
    assert sum(result['votes'].values()) == 3


def test_malformed_reports_excluded_from_the_tally(env):
    configure(env, *PROVIDERS)
    double = install(env)
    double.responders[HOST['gemini']] = lambda body: completion('not a report at all')
    run_id = create_run()
    seed_answers(run_id)

    client_as().post(f'/api/v1/compare/runs/{run_id}/reports')
    result = get_run(run_id)['tally']

    assert {'judge': 'gemini', 'reason': 'malformed'} in result['excluded']
    assert 'gemini' not in result['included_judges']
    assert result['partial'] is True


def test_tallies_for_ties(env):
    configure(env, *PROVIDERS)
    double = install(env)
    for p in PROVIDERS:
        double.responders[HOST[p]] = lambda body: completion(
            report_for(labels_in(body), kind='tie', named=labels_in(body)[:2])
        )
    run_id = create_run()
    seed_answers(run_id)

    client_as().post(f'/api/v1/compare/runs/{run_id}/reports')
    result = get_run(run_id)['tally']

    assert len(result['ties']) == 3
    assert sum(result['votes'].values()) == 0, 'a tie names no sole winner'
    assert result['n_included'] == 3, 'but it still counts in the denominator'
    assert result['outcome']['kind'] == 'no_majority'


def test_no_reliable_winner(env):
    configure(env, *PROVIDERS)
    double = install(env)
    for p in PROVIDERS:
        double.responders[HOST[p]] = lambda body: completion(
            report_for(labels_in(body), kind='no_reliable_winner', named=[])
        )
    run_id = create_run()
    seed_answers(run_id)

    client_as().post(f'/api/v1/compare/runs/{run_id}/reports')
    result = get_run(run_id)['tally']

    assert sorted(result['inconclusive']) == PROVIDERS
    assert result['outcome']['kind'] == 'no_majority'


def test_partial_summaries(env):
    configure(env, *PROVIDERS)
    install(env)
    run_id = create_run()
    seed_answers(run_id)
    judge_run(run_id, 'chatgpt')
    judge_run(run_id, 'gemini')

    summary = client_as().post(f'/api/v1/compare/runs/{run_id}/summary').json()
    assert summary['partial'] is True
    assert summary['included_judges'] == ['chatgpt', 'gemini']
    assert 'Partial summary: 2 of 3 judges included.' in summary['narrative']
    assert '- VESQOR: not judged.' in summary['narrative']


def test_outdated_marking_after_regeneration_with_earlier_runs_unchanged(env):
    """Both halves: the regeneration outdates reviews and summary, and the
    earlier run keeps every row it had."""
    configure(env, *PROVIDERS)
    install(env)

    earlier = create_run(prompt='the earlier run')
    seed_answers(earlier)
    judge_run(earlier, 'chatgpt')
    client_as().post(f'/api/v1/compare/runs/{earlier}/summary')
    before = asyncio.run(_snapshot(earlier))

    current = create_run(prompt='the current run')
    seed_answers(current)
    judge_run(current, 'chatgpt')
    client_as().post(f'/api/v1/compare/runs/{current}/summary')
    assert get_run(current)['reports'][0]['outdated'] is False

    generate(current, 'vesqor')  # a new complete revision moves the answer set

    after_regeneration = get_run(current)
    assert any(r['outdated'] for r in after_regeneration['reports'] if r['current'])
    assert after_regeneration['summary']['current']['outdated'] is True

    assert asyncio.run(_snapshot(earlier)) == before, 'the earlier run changed'
    assert get_run(earlier)['summary']['current']['outdated'] is False


def test_reopen_and_rerun(env):
    configure(env, *PROVIDERS)
    install(env)
    source = create_run(prompt='the original question', reference='the original reference')
    seed_answers(source)
    judge_run(source, 'chatgpt')

    # Reopen: everything saved comes back.
    reopened = get_run(source)
    assert reopened['run']['prompt'] == 'the original question'
    assert reopened['run']['reference'] == 'the original reference'
    assert [a['provider'] for a in reopened['answers']] == PROVIDERS
    assert reopened['reports'][0]['current'] is not None

    # Rerun: a new run, prompt and reference copied, the source intact.
    before = asyncio.run(_snapshot(source))
    rerun = client_as().post('/api/v1/compare/runs', json={'rerun_of_run_id': source}).json()
    assert rerun['run']['id'] != source
    assert rerun['run']['prompt'] == 'the original question'
    assert rerun['run']['reference'] == 'the original reference'
    assert rerun['run']['rerun_of_run_id'] == source
    assert asyncio.run(_snapshot(source)) == before

    assert get_run(source)['run']['rerun_count'] == 1
    assert get_run(rerun['run']['id'])['run']['rerun_of']['run_id'] == source


def test_oversized_input(env):
    configure(env, *PROVIDERS)
    double = install(env)
    env.setenv('ANSWER_COMPARE_CHATGPT_MAX_INPUT_CHARS', '50')
    env.setenv('ANSWER_COMPARE_GEMINI_JUDGE_MAX_INPUT_CHARS', '80')
    run_id = create_run(prompt='x' * 200)

    generation = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/chatgpt')
    assert generation.status_code == 413
    assert generation.json()['detail']['limit_chars'] == 50
    assert double.sent_to('chatgpt') == []

    seed_answers(run_id)
    judging = client_as().post(f'/api/v1/compare/runs/{run_id}/reports/gemini')
    assert judging.status_code == 413
    assert judging.json()['detail']['limit_chars'] == 80, 'judging has its own limit'
    assert double.sent_to('gemini') == []


def test_missing_integration_state(env):
    configure(env, 'chatgpt')  # gemini and vesqor unconfigured
    double = install(env)

    config = client_as().get('/api/v1/compare/config').json()['providers']
    by_id = {p['id']: p for p in config}
    assert by_id['chatgpt']['configured'] is True
    assert by_id['gemini']['configured'] is False
    assert 'ANSWER_COMPARE_GEMINI_API_KEY' in by_id['gemini']['missing']
    assert DUMMY_KEY not in json.dumps(config), 'never a key, only variable names'

    run_id = create_run()
    refusal = client_as().post(f'/api/v1/compare/runs/{run_id}/answers/gemini')
    assert refusal.status_code == 503
    assert refusal.json()['detail']['code'] == 'not_configured'
    assert double.sent_to('gemini') == []


def test_rejection_of_non_admin_requests(env):
    configure(env, *PROVIDERS)
    install(env)
    run_id = create_run()
    seed_answers(run_id)
    api = client_as('user')

    for method, path in (
        ('get', '/api/v1/compare/config'),
        ('get', '/api/v1/compare/runs'),
        ('post', '/api/v1/compare/runs'),
        ('get', f'/api/v1/compare/runs/{run_id}'),
        ('post', f'/api/v1/compare/runs/{run_id}/answers/chatgpt'),
        ('post', f'/api/v1/compare/runs/{run_id}/reports/chatgpt'),
        ('post', f'/api/v1/compare/runs/{run_id}/reports'),
        ('post', f'/api/v1/compare/runs/{run_id}/summary'),
    ):
        response = getattr(api, method)(
            path, **({'json': {'prompt': 'x'}} if method == 'post' and path.endswith('/runs') else {})
        )
        assert response.status_code == 401, f'{method.upper()} {path}'


async def _snapshot(run_id: str) -> dict[str, Any]:
    from open_webui.internal.db import get_async_db_context

    def dump(row) -> dict:
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    async with get_async_db_context() as db:
        run = await db.get(AnswerCompareRun, run_id)
        out: dict[str, Any] = {'run': dump(run)}
        for key, model in (
            ('answers', AnswerCompareAnswer),
            ('reports', AnswerCompareReport),
            ('summaries', AnswerCompareSummary),
        ):
            result = await db.execute(select(model).where(model.run_id == run_id).order_by(model.id))
            out[key] = [dump(row) for row in result.scalars().all()]
        return out


####################
# The matrix itself
####################

ACCEPTANCE_MATRIX: dict[str, str] = {
    'all three answers generating': 'test_all_three_answers_generating',
    'partial provider failure': 'test_partial_provider_failure',
    'each judge alone': 'test_each_judge_alone',
    'all three judges with no cross-visibility': 'test_all_three_judges_with_no_cross_visibility',
    'two-answer judging': 'test_two_answer_judging',
    'correct mapping of anonymous labels': 'test_correct_mapping_of_anonymous_labels',
    'self-vote flags': 'test_self_vote_flags',
    'malformed reports excluded from the tally': 'test_malformed_reports_excluded_from_the_tally',
    'tallies for ties': 'test_tallies_for_ties',
    'no reliable winner': 'test_no_reliable_winner',
    'partial summaries': 'test_partial_summaries',
    'outdated marking after regeneration with earlier runs unchanged': (
        'test_outdated_marking_after_regeneration_with_earlier_runs_unchanged'
    ),
    'reopen and rerun': 'test_reopen_and_rerun',
    'oversized input': 'test_oversized_input',
    'missing-integration state': 'test_missing_integration_state',
    'rejection of non-admin requests': 'test_rejection_of_non_admin_requests',
}


def test_zz_matrix_covers_every_acceptance_requirement():
    """No missing keys, no extras, and every named test really exists here."""
    assert set(ACCEPTANCE_MATRIX) == set(ACCEPTANCE_REQUIREMENTS), {
        'missing': sorted(set(ACCEPTANCE_REQUIREMENTS) - set(ACCEPTANCE_MATRIX)),
        'extra': sorted(set(ACCEPTANCE_MATRIX) - set(ACCEPTANCE_REQUIREMENTS)),
    }
    assert len(ACCEPTANCE_REQUIREMENTS) == 16
    assert len(set(ACCEPTANCE_MATRIX.values())) == 16, 'one test per requirement'

    module = globals()
    for requirement, test_name in ACCEPTANCE_MATRIX.items():
        assert test_name in module, f'{requirement}: {test_name} is not defined in this file'
        assert callable(module[test_name]), test_name
