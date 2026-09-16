"""VQ-25 stage 4a: the tally in code, self-vote flags, run all judges.

Run from the repository root:  pytest test/test_vq25_tally.py -q

The tally is a pure module, so most of this file builds inputs directly and
needs neither a database nor a double. The run-all endpoint tests reuse the
stage-3 machinery (MockTransport over the client's build seam, tables created
with create_all on the conftest database).
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
    STATUS_PENDING,
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
from sqlalchemy import select

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


PROVIDERS = list(providers.GENERATOR_IDS)  # chatgpt, gemini, vesqor
V1 = [{'provider': p, 'revision': 1} for p in PROVIDERS]
V2 = [
    {'provider': 'chatgpt', 'revision': 1},
    {'provider': 'gemini', 'revision': 1},
    {'provider': 'vesqor', 'revision': 2},
]

# The label map used by every pure test: A=vesqor, B=chatgpt, C=gemini.
LABEL_MAP = {'A': 'vesqor', 'B': 'chatgpt', 'C': 'gemini'}
LABEL_OF = {provider: label for label, provider in LABEL_MAP.items()}


####################
# Pure inputs
####################


def raw_report(kind: str, providers_named: list[str]) -> dict[str, Any]:
    """A validated-shaped report whose verdict names the given providers (via LABEL_MAP)."""
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
            for label in LABEL_MAP
        ],
        'verdict': {'kind': kind, 'labels': [LABEL_OF[p] for p in providers_named]},
        'rationale': 'because',
        'needs_verification': [],
    }


def report(kind: str, providers_named: list[str], versions=None, revision: int = 1) -> tally.TallyReport:
    return tally.TallyReport(
        revision=revision,
        judged_versions=versions if versions is not None else V1,
        label_map=dict(LABEL_MAP),
        report=raw_report(kind, providers_named),
    )


def winner(p: str, **kw) -> tally.TallyReport:
    return report('winner', [p], **kw)


def tie(*ps: str, **kw) -> tally.TallyReport:
    return report('tie', list(ps), **kw)


def no_winner(**kw) -> tally.TallyReport:
    return report('no_reliable_winner', [], **kw)


def judges(**by_judge) -> list[tally.TallyJudge]:
    """judges(chatgpt=winner('vesqor'), gemini=None, vesqor=(report, attempt, configured))."""
    out = []
    for judge_id in PROVIDERS:
        spec = by_judge.get(judge_id)
        if spec is None:
            out.append(tally.TallyJudge(judge=judge_id))
        elif isinstance(spec, tally.TallyReport):
            out.append(tally.TallyJudge(judge=judge_id, current=spec))
        else:
            out.append(tally.TallyJudge(judge=judge_id, **spec))
    return out


def attempt(status: str, code: Optional[str] = None, revision: int = 1) -> tally.TallyAttempt:
    return tally.TallyAttempt(revision=revision, status=status, error_code=code)


####################
# 1 — strict majority
####################


def test_two_of_three_is_preferred():
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=winner('vesqor'), vesqor=winner('chatgpt')))
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'vesqor'}
    assert t['votes'] == {'chatgpt': 1, 'gemini': 0, 'vesqor': 2}
    assert t['n_included'] == 3
    assert t['partial'] is False
    assert t['included_judges'] == PROVIDERS
    assert t['included_reports'] == [{'judge': j, 'revision': 1} for j in PROVIDERS]
    assert t['excluded'] == []


def test_one_of_two_is_not_a_strict_majority():
    """Exactly half is not a majority: 1 winner vote of 2 included -> no_majority."""
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=no_winner()))
    assert t['n_included'] == 2
    assert t['votes']['vesqor'] == 1
    assert t['outcome'] == {'kind': 'no_majority', 'provider': None}
    # And with a tie as the other verdict, likewise.
    t2 = tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=tie('chatgpt', 'gemini')))
    assert t2['outcome']['kind'] == 'no_majority'


def test_one_winner_plus_two_inconclusive_is_no_majority():
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=no_winner(), vesqor=no_winner()))
    assert t['outcome'] == {'kind': 'no_majority', 'provider': None}
    assert t['votes']['vesqor'] == 1 and t['n_included'] == 3
    assert t['inconclusive'] == ['gemini', 'vesqor']


def test_one_one_one_is_no_majority():
    t = tally.compute_tally(V1, judges(chatgpt=winner('gemini'), gemini=winner('vesqor'), vesqor=winner('chatgpt')))
    assert t['outcome']['kind'] == 'no_majority'
    assert t['votes'] == {'chatgpt': 1, 'gemini': 1, 'vesqor': 1}


def test_three_of_three_is_preferred():
    t = tally.compute_tally(V1, judges(chatgpt=winner('gemini'), gemini=winner('gemini'), vesqor=winner('gemini')))
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'gemini'}
    assert t['self_votes'] == [{'judge': 'gemini', 'kind': 'winner'}]


def test_one_of_one_is_not_preferred_owner_decision_014():
    """A strict majority of one is not a preference (DECISIONS.md#014).

    This test was inverted when the owner raised the threshold: before, a single
    included verdict naming a winner produced ``preferred``. The vote is still
    counted and still visible — only the outcome changed.
    """
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor')))
    assert t['outcome'] == {'kind': 'no_majority', 'provider': None}
    assert t['n_included'] == 1
    assert t['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 1}
    assert t['verdicts'] == [{'judge': 'chatgpt', 'kind': 'winner', 'providers': ['vesqor']}]
    assert t['partial'] is True
    assert t['excluded'] == [{'judge': 'gemini', 'reason': 'no_report'}, {'judge': 'vesqor', 'reason': 'no_report'}]


def test_two_of_two_is_preferred():
    """The threshold is two included judges, not three: 2 of 2 still qualifies."""
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=winner('vesqor')))
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'vesqor'}
    assert t['n_included'] == 2
    assert t['partial'] is True


####################
# 2, 3 — ties and no reliable winner
####################


def test_tie_counts_in_the_denominator_only():
    t = tally.compute_tally(
        V1, judges(chatgpt=winner('vesqor'), gemini=tie('chatgpt', 'vesqor'), vesqor=winner('vesqor'))
    )
    assert t['n_included'] == 3
    assert t['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 2}
    assert t['ties'] == [{'judge': 'gemini', 'providers': ['chatgpt', 'vesqor']}]
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'vesqor'}


def test_all_ties_is_no_majority_with_three_tie_entries():
    t = tally.compute_tally(
        V1, judges(chatgpt=tie('chatgpt', 'gemini'), gemini=tie('gemini', 'vesqor'), vesqor=tie('chatgpt', 'vesqor'))
    )
    assert t['outcome']['kind'] == 'no_majority'
    assert len(t['ties']) == 3
    assert t['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 0}
    assert t['n_included'] == 3


def test_all_no_reliable_winner():
    t = tally.compute_tally(V1, judges(chatgpt=no_winner(), gemini=no_winner(), vesqor=no_winner()))
    assert t['outcome']['kind'] == 'no_majority'
    assert t['inconclusive'] == PROVIDERS
    assert t['verdicts'] == [{'judge': j, 'kind': 'no_reliable_winner', 'providers': []} for j in PROVIDERS]


####################
# 4 — malformed excluded, unless an earlier fresh report stands
####################


def test_malformed_current_attempt_without_a_fresh_report_is_excluded():
    t = tally.compute_tally(
        V1,
        judges(
            chatgpt=winner('vesqor'),
            gemini={'current': None, 'latest_attempt': attempt('failed', 'malformed_report')},
            vesqor={'current': None, 'latest_attempt': attempt('failed', 'timeout')},
        ),
    )
    assert t['excluded'] == [{'judge': 'gemini', 'reason': 'malformed'}, {'judge': 'vesqor', 'reason': 'failed'}]
    assert t['included_judges'] == ['chatgpt']
    assert t['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 1}


def test_malformed_retry_does_not_remove_an_earlier_fresh_report():
    t = tally.compute_tally(
        V1,
        judges(
            chatgpt=winner('vesqor'),
            gemini={
                'current': winner('vesqor', revision=1),
                'latest_attempt': attempt('failed', 'malformed_report', revision=2),
            },
        ),
    )
    assert 'gemini' in t['included_judges']
    assert t['included_reports'] == [{'judge': 'chatgpt', 'revision': 1}, {'judge': 'gemini', 'revision': 1}]
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'vesqor'}


####################
# 5 — outdated excluded (#012), with deterministic precedence
####################


def test_outdated_report_is_excluded_and_its_self_vote_is_history_only():
    t = tally.compute_tally(
        V2,
        judges(
            chatgpt=winner('gemini', versions=V2),
            gemini=winner('gemini', versions=V1),  # judged before vesqor was regenerated — and a self-vote
            vesqor=winner('gemini', versions=V2),
        ),
    )
    assert t['excluded'] == [{'judge': 'gemini', 'reason': 'outdated'}]
    assert t['included_judges'] == ['chatgpt', 'vesqor']
    assert t['votes'] == {'chatgpt': 0, 'gemini': 2, 'vesqor': 0}
    assert t['partial'] is True
    assert t['self_votes'] == []
    assert t['self_votes_excluded'] == [{'judge': 'gemini', 'kind': 'winner'}]
    assert t['current_versions'] == V2


def test_outdated_wins_over_a_failed_rejudge_and_reports_both():
    t = tally.compute_tally(
        V2,
        judges(
            gemini={
                'current': winner('vesqor', versions=V1, revision=1),
                'latest_attempt': attempt('failed', 'malformed_report', revision=2),
            }
        ),
    )
    assert t['excluded'][1] == {'judge': 'gemini', 'reason': 'outdated', 'latest_attempt': 'malformed'}
    t2 = tally.compute_tally(
        V2,
        judges(
            gemini={
                'current': winner('vesqor', versions=V1, revision=1),
                'latest_attempt': attempt('failed', 'timeout', revision=2),
            }
        ),
    )
    assert t2['excluded'][1] == {'judge': 'gemini', 'reason': 'outdated', 'latest_attempt': 'failed'}


def test_freshness_compares_as_sets():
    reordered = [V1[2], V1[0], V1[1]]
    t = tally.compute_tally(V1, judges(chatgpt=winner('vesqor', versions=reordered)))
    assert t['included_judges'] == ['chatgpt']


def test_exclusion_is_deterministic_across_runs():
    inputs = judges(
        chatgpt={'current': winner('vesqor', versions=V1), 'latest_attempt': attempt('failed', 'timeout', revision=2)},
        gemini={'current': None, 'latest_attempt': attempt('failed', 'malformed_report'), 'configured': False},
        vesqor={'current': None, 'configured': False},
    )
    first = tally.compute_tally(V2, inputs)
    second = tally.compute_tally(V2, inputs)
    assert first == second
    assert first['excluded'] == [
        {'judge': 'chatgpt', 'reason': 'outdated', 'latest_attempt': 'failed'},
        {'judge': 'gemini', 'reason': 'malformed'},
        {'judge': 'vesqor', 'reason': 'not_configured'},
    ]


def test_unconfigured_judge_with_a_fresh_report_is_still_included():
    t = tally.compute_tally(V1, judges(chatgpt={'current': winner('vesqor'), 'configured': False}))
    assert t['included_judges'] == ['chatgpt']


####################
# 6 — self-vote flags annotate and never change the count
####################


def test_self_vote_flags_winner_and_tie():
    t = tally.compute_tally(
        V1, judges(chatgpt=winner('chatgpt'), gemini=tie('gemini', 'vesqor'), vesqor=winner('chatgpt'))
    )
    assert t['self_votes'] == [{'judge': 'chatgpt', 'kind': 'winner'}, {'judge': 'gemini', 'kind': 'tie'}]
    assert t['votes'] == {'chatgpt': 2, 'gemini': 0, 'vesqor': 0}
    assert t['outcome'] == {'kind': 'preferred', 'provider': 'chatgpt'}


def test_a_self_vote_counts_exactly_like_any_other_vote():
    with_self = tally.compute_tally(
        V1, judges(chatgpt=winner('chatgpt'), gemini=winner('vesqor'), vesqor=winner('gemini'))
    )
    without = tally.compute_tally(
        V1, judges(chatgpt=winner('vesqor'), gemini=winner('chatgpt'), vesqor=winner('gemini'))
    )
    # Same multiset of votes, different owners: identical counts and outcome.
    assert with_self['votes'] == without['votes'] == {'chatgpt': 1, 'gemini': 1, 'vesqor': 1}
    assert with_self['outcome'] == without['outcome']
    assert with_self['self_votes'] == [{'judge': 'chatgpt', 'kind': 'winner'}]
    assert without['self_votes'] == []

    strip = lambda t: {k: v for k, v in t.items() if k not in ('self_votes', 'self_votes_excluded', 'verdicts')}  # noqa: E731
    assert strip(with_self)['votes'] == strip(without)['votes']


####################
# 7, 8 — partial and zero included
####################


def test_partial_when_fewer_than_three_included():
    assert tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=winner('vesqor')))['partial'] is True
    assert (
        tally.compute_tally(V1, judges(chatgpt=winner('vesqor'), gemini=winner('vesqor'), vesqor=no_winner()))[
            'partial'
        ]
        is False
    )


def test_partial_is_relative_to_three_systems_not_configured_judges():
    t = tally.compute_tally(
        V1, judges(chatgpt=winner('vesqor'), gemini=winner('vesqor'), vesqor={'current': None, 'configured': False})
    )
    assert t['partial'] is True
    assert t['excluded'] == [{'judge': 'vesqor', 'reason': 'not_configured'}]


def test_zero_included():
    t = tally.compute_tally(V1, judges())
    assert t['outcome'] == {'kind': 'no_valid_verdicts', 'provider': None}
    assert t['votes'] == {'chatgpt': 0, 'gemini': 0, 'vesqor': 0}
    assert t['partial'] is True and t['n_included'] == 0
    assert t['included_reports'] == []


def all_keys(node: Any) -> set[str]:
    """Every dict key anywhere in a nested structure — the tally must not grow a
    confidence-like field at any depth, not only at the top."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(str(key))
            keys |= all_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= all_keys(item)
    return keys


def test_tally_carries_no_confidence_or_consensus_field():
    t = tally.compute_tally(
        V1,
        judges(
            chatgpt={'current': winner('vesqor'), 'latest_attempt': attempt('failed', 'timeout', revision=2)},
            gemini=tie('gemini', 'vesqor'),
            vesqor={
                'current': winner('vesqor', versions=V2),
                'latest_attempt': attempt('failed', 'malformed_report', revision=2),
            },
        ),
    )
    forbidden = ('confidence', 'consensus', 'score', 'certainty', 'agreement')
    offenders = sorted(key for key in all_keys(t) if any(word in key.lower() for word in forbidden))
    assert offenders == [], offenders
    # The helper really walks: it sees nested keys such as the excluded entry's fields.
    assert {'latest_attempt', 'reason', 'provider', 'kind'} <= all_keys(t)


def test_unmappable_stored_report_is_excluded_not_fatal():
    broken = tally.TallyReport(
        revision=1, judged_versions=V1, label_map={'A': 'vesqor'}, report=raw_report('winner', ['vesqor'])
    )
    t = tally.compute_tally(V1, judges(chatgpt=broken, gemini=winner('vesqor')))
    assert t['excluded'] == [{'judge': 'chatgpt', 'reason': 'unmappable'}, {'judge': 'vesqor', 'reason': 'no_report'}]
    assert t['included_judges'] == ['gemini']


####################
# 9–11 — run all judges (endpoint, doubles)
####################

DUMMY_KEY = 'sk-vq25-tally-secret-do-not-leak'
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
PROVIDER_MODEL = {'chatgpt': 'gpt-test-1', 'gemini': 'gemini-test-1', 'vesqor': 'vesqor-reasoning'}
ANSWER_TEXT = {'chatgpt': 'chatgpt answer text', 'gemini': 'gemini answer text', 'vesqor': 'vesqor answer text'}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for names in PROVIDER_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for provider_id in PROVIDERS:
        monkeypatch.delenv(providers.max_input_chars_env(provider_id), raising=False)
        monkeypatch.delenv(providers.judge_max_input_chars_env(provider_id), raising=False)
    monkeypatch.delenv(provider_client.ENV_REQUEST_TIMEOUT_SECONDS, raising=False)
    judge.reset_mode_cache()
    asyncio.run(_create_tables())
    return monkeypatch


def configure(monkeypatch, *provider_ids: str) -> None:
    for provider_id in provider_ids:
        base_url_env, key_env, model_env = PROVIDER_ENV[provider_id]
        monkeypatch.setenv(base_url_env, PROVIDER_HOST[provider_id])
        monkeypatch.setenv(key_env, DUMMY_KEY)
        monkeypatch.setenv(model_env, PROVIDER_MODEL[provider_id])


def labels_in(body: dict) -> list[str]:
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    return [line.split()[2] for line in user.splitlines() if line.startswith('=== ANSWER ')]


def valid_report_for(labels: list[str], winner_label: str, rationale: str = 'fine') -> dict:
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
        'verdict': {'kind': 'winner', 'labels': [winner_label]},
        'rationale': rationale,
        'needs_verification': [],
    }


def completion(content) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={'model': 'judge-model', 'choices': [{'message': {'content': text}}]})


class Double:
    def __init__(self):
        self.requests: list[dict] = []
        self.responders: dict[str, Any] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        url = str(request.url)
        self.requests.append({'url': url, 'body': body, 'headers': dict(request.headers)})
        for host, responder in self.responders.items():
            if url.startswith(host):
                return responder(body)
        return completion(valid_report_for(labels_in(body), labels_in(body)[0]))

    def requests_for(self, host: str) -> list[dict]:
        return [r for r in self.requests if r['url'].startswith(host)]


def install_double(monkeypatch) -> Double:
    double = Double()

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout)

    monkeypatch.setattr(provider_client, '_build_async_client', build)
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


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    tables = [m.__table__ for m in (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


def make_run_with_answers(which: tuple[str, ...] = tuple(PROVIDERS)) -> str:
    async def _seed():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id', admin_email='admin@example.com', form=AnswerCompareRunForm(prompt='q', reference=None)
        )
        for provider_id in which:
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run.id,
                provider=provider_id,
                status=STATUS_COMPLETE,
                requested_model='m',
                text=ANSWER_TEXT[provider_id],
            )
        return run.id

    return asyncio.run(_seed())


def stored_reports(run_id: str) -> list[dict]:
    async def _rows():
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            result = await db.execute(
                select(AnswerCompareReport)
                .where(AnswerCompareReport.run_id == run_id)
                .order_by(AnswerCompareReport.judge, AnswerCompareReport.revision)
            )
            return [
                {
                    'id': r.id,
                    'judge': r.judge,
                    'revision': r.revision,
                    'status': r.status,
                    'label_map': r.label_map,
                    'report': r.report,
                    'error': r.error,
                }
                for r in result.scalars().all()
            ]

    return asyncio.run(_rows())


def run_all_url(run_id: str) -> str:
    return f'/api/v1/compare/runs/{run_id}/reports'


def test_run_all_three_judges_complete_with_distinct_shuffles_and_no_cross_visibility(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)
    markers = {p: secrets.token_hex(8) for p in PROVIDERS}
    for provider_id in PROVIDERS:
        double.responders[PROVIDER_HOST[provider_id]] = lambda body, m=markers[provider_id]: completion(
            valid_report_for(labels_in(body), labels_in(body)[0], rationale=f'marker {m}')
        )
    # Three judges, three seeded shuffles that differ from each other.
    seeds = iter([0, 1, 4])  # three seeds, three distinct permutations
    import random

    isolated_env.setattr(judge, 'default_rng', lambda: random.Random(next(seeds)))

    run_id = make_run_with_answers()
    response = client_as().post(run_all_url(run_id))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [r['judge'] for r in payload['results']] == PROVIDERS
    assert all(r['status'] == 'complete' for r in payload['results'])

    label_maps = [json.dumps(r['report']['label_map'], sort_keys=True) for r in payload['results']]
    assert len(set(label_maps)) == 3

    # No judge saw any other judge's report.
    for provider_id in PROVIDERS:
        sent = json.dumps(double.requests_for(PROVIDER_HOST[provider_id]))
        for other, marker in markers.items():
            if other != provider_id:
                assert marker not in sent

    # The tally is in the response and consistent with the three stored reports.
    assert payload['tally']['n_included'] == 3
    assert payload['tally']['partial'] is False
    assert payload['tally']['included_reports'] == [{'judge': j, 'revision': 1} for j in PROVIDERS]
    assert sum(payload['tally']['votes'].values()) == 3
    rows = stored_reports(run_id)
    assert [(r['judge'], r['status']) for r in rows] == [(j, STATUS_COMPLETE) for j in PROVIDERS]


def test_run_all_one_judge_failing_leaves_the_others_untouched(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)
    double.responders[PROVIDER_HOST['gemini']] = lambda body: httpx.Response(500, json={'error': {'message': 'boom'}})

    run_id = make_run_with_answers()
    payload = client_as().post(run_all_url(run_id)).json()
    by_judge = {r['judge']: r for r in payload['results']}

    assert by_judge['gemini']['status'] == 'failed'
    assert by_judge['gemini']['report']['error']['code'] == 'upstream_error'
    assert by_judge['chatgpt']['status'] == 'complete'
    assert by_judge['vesqor']['status'] == 'complete'

    rows = {r['judge']: r for r in stored_reports(run_id)}
    assert rows['chatgpt']['status'] == STATUS_COMPLETE and rows['chatgpt']['report'] is not None
    assert rows['vesqor']['status'] == STATUS_COMPLETE and rows['vesqor']['report'] is not None
    assert rows['gemini']['status'] == STATUS_FAILED

    assert payload['tally']['included_judges'] == ['chatgpt', 'vesqor']
    assert payload['tally']['excluded'] == [{'judge': 'gemini', 'reason': 'failed'}]
    assert payload['tally']['partial'] is True


def test_run_all_unexpected_exception_becomes_that_judges_internal_entry(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)

    def explode(body):
        raise RuntimeError('something nobody anticipated')

    double.responders[PROVIDER_HOST['vesqor']] = explode

    run_id = make_run_with_answers()
    response = client_as().post(run_all_url(run_id))

    assert response.status_code == 200
    by_judge = {r['judge']: r for r in response.json()['results']}
    assert by_judge['vesqor'] == {'judge': 'vesqor', 'status': 'failed', 'report': None, 'reason': {'code': 'internal'}}
    assert by_judge['chatgpt']['status'] == 'complete'
    assert by_judge['gemini']['status'] == 'complete'

    rows = {r['judge']: r for r in stored_reports(run_id)}
    assert rows['chatgpt']['status'] == STATUS_COMPLETE
    assert rows['gemini']['status'] == STATUS_COMPLETE
    # The pending row that was written before the call is settled, not left hanging.
    assert rows['vesqor']['status'] == STATUS_FAILED
    assert json.loads(rows['vesqor']['error'])['code'] == 'internal'


def test_run_all_unconfigured_judge_is_skipped_and_nothing_is_sent_for_it(isolated_env):
    configure(isolated_env, 'chatgpt', 'vesqor')
    double = install_double(isolated_env)

    run_id = make_run_with_answers()
    payload = client_as().post(run_all_url(run_id)).json()
    by_judge = {r['judge']: r for r in payload['results']}

    assert by_judge['gemini']['status'] == 'skipped'
    assert by_judge['gemini']['reason']['code'] == 'not_configured'
    assert 'ANSWER_COMPARE_GEMINI_API_KEY' in by_judge['gemini']['reason']['missing']
    assert double.requests_for(PROVIDER_HOST['gemini']) == []
    assert by_judge['chatgpt']['status'] == 'complete' and by_judge['vesqor']['status'] == 'complete'
    assert [r['judge'] for r in stored_reports(run_id)] == ['chatgpt', 'vesqor']
    assert payload['tally']['excluded'] == [{'judge': 'gemini', 'reason': 'not_configured'}]


def test_run_all_with_fewer_than_two_answers_is_409_and_sends_nothing(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)

    run_id = make_run_with_answers(('chatgpt',))
    response = client_as().post(run_all_url(run_id))

    assert response.status_code == 409
    assert response.json()['detail'] == {'code': 'not_enough_answers', 'complete': ['chatgpt']}
    assert double.requests == []
    assert stored_reports(run_id) == []


def test_run_all_already_running_judge_is_skipped(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)
    run_id = make_run_with_answers()

    asyncio.run(AnswerCompareReports.insert_next_revision(run_id=run_id, judge='chatgpt', status=STATUS_PENDING))

    by_judge = {r['judge']: r for r in client_as().post(run_all_url(run_id)).json()['results']}
    assert by_judge['chatgpt']['status'] == 'skipped'
    assert by_judge['chatgpt']['reason']['code'] == 'already_running'
    assert double.requests_for(PROVIDER_HOST['chatgpt']) == []
    assert by_judge['gemini']['status'] == 'complete'


def test_run_all_rejects_a_non_admin(isolated_env):
    run_id = make_run_with_answers()
    assert client_as('user').post(run_all_url(run_id)).status_code == 401


def test_get_carries_a_tally_consistent_with_its_reports(isolated_env):
    configure(isolated_env, *PROVIDERS)
    install_double(isolated_env)
    run_id = make_run_with_answers()
    api = client_as()

    assert api.post(run_all_url(run_id)).status_code == 200
    read = api.get(f'/api/v1/compare/runs/{run_id}').json()

    t = read['tally']
    current = {r['judge']: r['current'] for r in read['reports']}
    assert t['included_reports'] == [{'judge': j, 'revision': current[j]['revision']} for j in PROVIDERS]
    for verdict in t['verdicts']:
        assert verdict['providers'] == current[verdict['judge']]['mapped']['verdict']['providers']
    assert t['outcome']['kind'] in ('preferred', 'no_majority')

    # Regenerate one answer: every report is now outdated and the tally says so.
    asyncio.run(
        AnswerCompareAnswers.insert_next_revision(
            run_id=run_id, provider='vesqor', status=STATUS_COMPLETE, text='new vesqor'
        )
    )
    after = api.get(f'/api/v1/compare/runs/{run_id}').json()['tally']
    assert after['outcome']['kind'] == 'no_valid_verdicts'
    assert [e['reason'] for e in after['excluded']] == ['outdated', 'outdated', 'outdated']
    assert after['partial'] is True


def test_no_key_material_in_run_all_or_tally(isolated_env):
    configure(isolated_env, *PROVIDERS)
    double = install_double(isolated_env)
    double.responders[PROVIDER_HOST['gemini']] = lambda body: httpx.Response(
        400, json={'error': {'message': f'rejected; auth was Bearer {DUMMY_KEY}'}}
    )
    run_id = make_run_with_answers()
    api = client_as()

    text = api.post(run_all_url(run_id)).text
    assert DUMMY_KEY not in text
    assert DUMMY_KEY not in api.get(f'/api/v1/compare/runs/{run_id}').text
    assert DUMMY_KEY not in json.dumps(stored_reports(run_id))
