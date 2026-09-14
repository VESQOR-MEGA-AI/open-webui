"""VQ-25 stage 6a: the run list, lineage, and cross-run isolation.

Run from the repository root:  pytest test/test_vq25_history.py -q
"""

import asyncio
import json
import pathlib
from typing import Any, Optional

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
    AnswerCompareSummaries,
    AnswerCompareSummary,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils.auth import get_current_user
from sqlalchemy import delete, event, select

PROVIDERS = list(providers.PROVIDER_IDS)
DUMMY_KEY = 'sk-vq25-history-secret-do-not-leak'


@pytest.fixture(autouse=True)
def tables():
    """Fresh tables per test: the list endpoint reads everything, so rows left by
    a neighbouring test would leak into every assertion about the page."""
    asyncio.run(_create_tables())
    asyncio.run(_clear_tables())


async def _clear_tables() -> None:
    from open_webui.internal.db import get_async_db_context

    async with get_async_db_context() as db:
        for model in (AnswerCompareSummary, AnswerCompareReport, AnswerCompareAnswer, AnswerCompareRun):
            await db.execute(delete(model))
        await db.commit()


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    models = (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[m.__table__ for m in models]))


def _user(role: str) -> UserModel:
    return UserModel(
        id=f'{role}-id', email=f'{role}@example.com', name=role, role=role, last_active_at=0, updated_at=0, created_at=0
    )


def client_as(role: str = 'admin') -> TestClient:
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def make_run(
    prompt: str = 'a question',
    reference: Optional[str] = None,
    rerun_of: Optional[str] = None,
    created_at: Optional[int] = None,
    admin_email: str = 'admin@example.com',
) -> str:
    async def _seed():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email=admin_email,
            form=AnswerCompareRunForm(prompt=prompt, reference=reference, rerun_of_run_id=rerun_of),
        )
        if created_at is not None:
            from open_webui.internal.db import get_async_db_context

            async with get_async_db_context() as db:
                row = await db.get(AnswerCompareRun, run.id)
                row.created_at = created_at
                await db.commit()
        return run.id

    return asyncio.run(_seed())


def add_answer(run_id: str, provider: str, status: str = STATUS_COMPLETE, text: str = 'answer') -> None:
    asyncio.run(
        AnswerCompareAnswers.insert_next_revision(
            run_id=run_id, provider=provider, status=status, text=text if status == STATUS_COMPLETE else None
        )
    )


def add_report(run_id: str, judge: str, status: str = STATUS_COMPLETE) -> None:
    asyncio.run(
        AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge=judge,
            status=status,
            label_map={'A': 'chatgpt', 'B': 'gemini', 'C': 'vesqor'},
            report={'verdict': {'kind': 'winner', 'labels': ['A']}} if status == STATUS_COMPLETE else None,
            judged_versions=[{'provider': p, 'revision': 1} for p in PROVIDERS],
        )
    )


def add_summary(run_id: str) -> None:
    asyncio.run(
        AnswerCompareSummaries.insert_next_revision(
            run_id=run_id, narrative='n', tally={}, judged_versions=[], partial=True, included_judges=['chatgpt']
        )
    )


def list_runs(**params) -> dict:
    response = client_as().get('/api/v1/compare/runs', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def row_for(run_id: str, **params) -> dict:
    """The list row of a specific run.

    Never index the page: `created_at` is whole seconds, so two runs created in
    the same second are ordered by their uuid — stable between requests, but
    arbitrary between runs. "The one I just made is first" is not true.
    """
    rows = {row['id']: row for row in list_runs(**params)['runs']}
    assert run_id in rows, f'{run_id} not in the page: {sorted(rows)}'
    return rows[run_id]


####################
# The list
####################


def test_empty_list_when_there_are_no_runs():
    payload = list_runs()
    assert payload['runs'] == []
    assert payload['next_before'] is None


def test_newest_first_ordering_uses_created_at():
    """Ordering is by `created_at`; distinct timestamps, because two runs in the
    same second are ordered by uuid and 'newest' has no meaning between them."""
    oldest = make_run(prompt='oldest', created_at=1000)
    middle = make_run(prompt='middle', created_at=2000)
    newest = make_run(prompt='newest', created_at=3000)

    assert [row['id'] for row in list_runs()['runs']] == [newest, middle, oldest]


def test_newest_first_with_limit_and_paging_on_a_compound_cursor():
    # Three runs sharing one timestamp, straddling a page boundary: paging on
    # created_at alone would drop the middle one entirely.
    ids = [make_run(prompt=f'run {i}', created_at=1000) for i in range(3)]
    older = make_run(prompt='older', created_at=900)

    first = list_runs(limit=3)
    assert len(first['runs']) == 3
    assert first['next_before'] == 1000
    assert first['next_before_id'] == first['runs'][-1]['id']

    second = list_runs(limit=3, before=first['next_before'], before_id=first['next_before_id'])
    seen = [row['id'] for row in first['runs'] + second['runs']]
    assert sorted(seen) == sorted(ids + [older]), 'no run fell between pages'
    assert len(seen) == len(set(seen)), 'no run appeared on two pages'
    assert second['next_before'] is None, 'a short page asks for nothing older'


def test_limit_is_capped_and_validated():
    make_run()
    assert client_as().get('/api/v1/compare/runs', params={'limit': 101}).status_code == 422
    assert client_as().get('/api/v1/compare/runs', params={'limit': 0}).status_code == 422
    assert len(list_runs(limit=100)['runs']) == 1


def test_before_returns_strictly_older_runs():
    """The cursor is a row, not a moment: 'everything after this row in the
    ordering'. Passing a run's own cursor must exclude that run itself."""
    newer = make_run(prompt='new', created_at=2000)
    make_run(prompt='old', created_at=1000)
    same_second = make_run(prompt='same second', created_at=2000)

    payload = list_runs(before=2000, before_id=newer)
    assert 'new' not in [row['prompt_excerpt'] for row in payload['runs']], 'the cursor row itself is excluded'
    assert 'old' in [row['prompt_excerpt'] for row in payload['runs']]

    # Walk the whole history one row at a time: every run appears exactly once,
    # including the two sharing a second — the case a timestamp-only cursor loses.
    seen: list[str] = []
    cursor: tuple[int, str] | None = None
    while True:
        page = list_runs(limit=1, **({'before': cursor[0], 'before_id': cursor[1]} if cursor else {}))
        if not page['runs']:
            break
        seen.append(page['runs'][0]['id'])  # limit=1, so this is the only row — not a position assumption
        if page['next_before'] is None:
            break
        cursor = (page['next_before'], page['next_before_id'])

    assert sorted(seen) == sorted(
        [newer, same_second] + [r['id'] for r in list_runs()['runs'] if r['prompt_excerpt'] == 'old']
    )
    assert len(seen) == len(set(seen)) == 3


def test_excerpt_is_truncated_and_the_full_prompt_is_not_shipped():
    long_prompt = 'x' * 500 + 'SECRET-TAIL'
    long_id = make_run(prompt=long_prompt, reference='some reference')

    row = row_for(long_id)
    assert row['prompt_excerpt'] == 'x' * 200
    assert len(row['prompt_excerpt']) == 200
    assert row['prompt_truncated'] is True
    assert 'SECRET-TAIL' not in json.dumps(row)
    assert row['has_reference'] is True

    short_id = make_run(prompt='short', reference='')
    short = row_for(short_id)
    assert short['prompt_excerpt'] == 'short'
    assert short['prompt_truncated'] is False
    assert short['has_reference'] is False


def test_counts_follow_the_projects_own_current_rule():
    """A failed revision 2 does not turn a complete revision 1 into a failure —
    and it still shows up as a failed attempt. Both halves, or an implementation
    that dropped the new field would pass."""
    run_id = make_run()
    add_answer(run_id, 'chatgpt', STATUS_COMPLETE)
    add_answer(run_id, 'chatgpt', STATUS_FAILED)  # revision 2 of the same provider
    add_answer(run_id, 'gemini', STATUS_COMPLETE)
    add_answer(run_id, 'vesqor', STATUS_PENDING)

    add_report(run_id, 'chatgpt', STATUS_COMPLETE)
    add_report(run_id, 'chatgpt', STATUS_FAILED)
    add_report(run_id, 'gemini', STATUS_FAILED)

    row = row_for(run_id)
    assert row['answers'] == {'complete': 2, 'failed_attempts': 1, 'pending': 1}
    assert row['reports'] == {'complete': 1, 'failed_attempts': 2}
    assert row['has_summary'] is False

    add_summary(run_id)
    assert row_for(run_id)['has_summary'] is True


def test_every_admins_runs_are_listed_and_each_row_names_its_author():
    """Recorded decision: the surface is admin-only, but prompts can hold anything,
    so a row says whose run it is rather than pretending the list is personal."""
    make_run(prompt='mine', admin_email='admin@example.com')
    make_run(prompt='theirs', admin_email='other@example.com')

    rows = list_runs()['runs']
    assert sorted(row['admin_email'] for row in rows) == ['admin@example.com', 'other@example.com']


def test_non_admin_is_rejected_and_no_key_material_is_returned(monkeypatch):
    monkeypatch.setenv('ANSWER_COMPARE_CHATGPT_API_KEY', DUMMY_KEY)
    run_id = make_run(prompt='q', reference='r')
    add_answer(run_id, 'chatgpt')

    assert client_as('user').get('/api/v1/compare/runs').status_code == 401
    assert DUMMY_KEY not in client_as().get('/api/v1/compare/runs').text


####################
# No N+1
####################


class StatementCounter:
    """Counts SQL statements at the engine, not around a session.

    `get_async_db_context` makes its own session, so a wrapper around "the"
    session is easily around the wrong object, and anything not going through
    `execute` would not be counted at all.
    """

    def __init__(self):
        self.statements: list[str] = []

    def __enter__(self):
        from open_webui.internal.db import async_engine

        self._engine = async_engine.sync_engine
        event.listen(self._engine, 'before_cursor_execute', self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, 'before_cursor_execute', self._on_execute)
        return False

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)


def test_round_trip_count_is_constant_in_the_number_of_runs():
    for index in range(3):
        run_id = make_run(prompt=f'small {index}')
        add_answer(run_id, 'chatgpt')
    with StatementCounter() as small:
        assert len(list_runs(limit=100)['runs']) == 3

    for index in range(22):
        run_id = make_run(prompt=f'big {index}')
        add_answer(run_id, 'gemini')
    with StatementCounter() as big:
        assert len(list_runs(limit=100)['runs']) == 25

    assert len(small.statements) == len(big.statements), (
        f'{len(small.statements)} statements for 3 runs vs {len(big.statements)} for 25:\n' + '\n'.join(big.statements)
    )
    # One page query plus three grouped aggregates; anything per-run would grow.
    assert len(small.statements) <= 6, small.statements


####################
# Lineage
####################


def test_lineage_resolves_the_parent_and_counts_children():
    parent = make_run(prompt='original', created_at=1500)
    child_a = make_run(prompt='again', rerun_of=parent)
    make_run(prompt='again and again', rerun_of=parent)

    api = client_as()
    parent_run = api.get(f'/api/v1/compare/runs/{parent}').json()['run']
    assert parent_run['rerun_of'] is None
    assert parent_run['rerun_count'] == 2

    child_run = api.get(f'/api/v1/compare/runs/{child_a}').json()['run']
    assert child_run['rerun_of'] == {'run_id': parent, 'created_at': 1500}
    assert child_run['rerun_count'] == 0
    assert row_for(child_a)['rerun_of_run_id'] == parent
    assert row_for(parent)['rerun_of_run_id'] is None


def test_lineage_degrades_when_the_parent_id_does_not_resolve():
    """There is no delete today; the response must not depend on that."""
    orphan = make_run(prompt='orphan', rerun_of='no-such-run')

    run = client_as().get(f'/api/v1/compare/runs/{orphan}').json()['run']
    assert run['rerun_of'] is None
    assert run['rerun_of_run_id'] == 'no-such-run'
    assert run['rerun_count'] == 0


####################
# Cross-run isolation — the second half of acceptance item 12
####################


async def _snapshot(run_id: str) -> dict[str, Any]:
    """Every stored row of a run, straight from the tables."""
    from open_webui.internal.db import get_async_db_context

    def dump(row) -> dict:
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    async with get_async_db_context() as db:
        out: dict[str, Any] = {}
        run = await db.get(AnswerCompareRun, run_id)
        out['run'] = dump(run)
        for key, model, order in (
            ('answers', AnswerCompareAnswer, AnswerCompareAnswer.provider),
            ('reports', AnswerCompareReport, AnswerCompareReport.judge),
            ('summaries', AnswerCompareSummary, AnswerCompareSummary.revision),
        ):
            result = await db.execute(select(model).where(model.run_id == run_id).order_by(order, model.id))
            out[key] = [dump(row) for row in result.scalars().all()]
        return out


def test_work_in_one_run_leaves_every_row_of_an_earlier_run_untouched():
    run_a = make_run(prompt='run A', reference='reference A')
    for provider in PROVIDERS:
        add_answer(run_a, provider, text=f'{provider} answer in A')
    add_report(run_a, 'chatgpt')
    add_summary(run_a)
    before = asyncio.run(_snapshot(run_a))
    assert before['answers'] and before['reports'] and before['summaries']

    run_b = make_run(prompt='run B', rerun_of=run_a)
    for provider in PROVIDERS:
        add_answer(run_b, provider, text=f'{provider} answer in B')
    add_answer(run_b, 'vesqor', text='regenerated in B')
    add_answer(run_b, 'gemini', STATUS_FAILED)
    add_report(run_b, 'gemini')
    add_summary(run_b)

    after = asyncio.run(_snapshot(run_a))
    assert after == before, 'work in run B changed a row of run A'

    # And the list still shows A with its own counts, unaffected by B.
    rows = {row['id']: row for row in list_runs()['runs']}
    assert rows[run_a]['answers'] == {'complete': 3, 'failed_attempts': 0, 'pending': 0}
    assert rows[run_b]['answers'] == {'complete': 3, 'failed_attempts': 1, 'pending': 0}


####################
# Recorded decisions — asserted so they cannot drift into defaults
####################


def test_run_status_is_written_active_and_read_nowhere():
    """Recorded decision (6a): `run.status` is deliberately unused. If a future
    change starts filtering on it, this test is where that shows up."""
    run_id = make_run()

    async def _status():
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            return (await db.get(AnswerCompareRun, run_id)).status

    assert asyncio.run(_status()) == 'active'

    router_source = (
        pathlib.Path(__file__).resolve().parent.parent / 'backend' / 'open_webui' / 'routers' / 'answer_compare.py'
    ).read_text(encoding='utf-8')
    # Serialised for the page, never used as a filter.
    assert 'status=run.status' in router_source
    assert 'AnswerCompareRun.status' not in router_source

    model_source = (
        pathlib.Path(__file__).resolve().parent.parent / 'backend' / 'open_webui' / 'models' / 'answer_compare.py'
    ).read_text(encoding='utf-8')
    assert 'Deliberately unused, recorded as a decision' in model_source
