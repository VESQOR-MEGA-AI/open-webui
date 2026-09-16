"""VQ-25 stage 5a: the summary — assembled in code, persisted, partial/outdated.

Run from the repository root:  pytest test/test_vq25_summary.py -q

The narrative module is pure, so most of this file builds inputs directly. The
golden fixture below is deliberately the whole expected text in a file beside
this one: presence/absence assertions cannot catch wording drift, and fixed
sections in a fixed order with fixed headings is the entire point of the stage.
"""

import ast
import asyncio
import json
import os
import pathlib
import subprocess
import tempfile
import sys
from pathlib import Path
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
    AnswerCompareSummaries,
    AnswerCompareSummary,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils import answer_compare_summary as summary
from open_webui.utils import answer_compare_tally as tally
from open_webui.utils.auth import get_current_user
from sqlalchemy import select

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).parent / 'fixtures' / 'vq25_summary_golden.txt'

PROVIDERS = list(providers.GENERATOR_IDS)
# vesqor is deliberately not label A in either map: a summary that assumed it was
# would pass a test built on the easy case and target the wrong answer in §8.
CHATGPT_MAP = {'A': 'chatgpt', 'B': 'vesqor', 'C': 'gemini'}
VESQOR_MAP = {'A': 'gemini', 'B': 'chatgpt', 'C': 'vesqor'}
GEMINI_MAP = {'A': 'vesqor', 'B': 'gemini', 'C': 'chatgpt'}

FRESH_VERSIONS = [
    {'provider': 'chatgpt', 'revision': 1},
    {'provider': 'gemini', 'revision': 1},
    {'provider': 'vesqor', 'revision': 2},
]
OLD_VERSIONS = [{'provider': p, 'revision': 1} for p in PROVIDERS]


def finding(passage: str, note: str) -> dict[str, str]:
    return {'passage': passage, 'note': note}


def answer_section(label: str, **fields: Any) -> dict[str, Any]:
    section = {
        'label': label,
        'strengths': [],
        'errors_or_unsupported': [],
        'omissions': [],
        'useful_extras': [],
        'unnecessary': [],
        'improvements': [],
    }
    section.update(fields)
    return section


def raw_report(
    label_map: dict[str, str],
    sections: dict[str, dict],
    kind: str,
    providers_named: list[str],
    rationale: str,
    needs_verification: list[str],
) -> dict[str, Any]:
    label_of = {provider: label for label, provider in label_map.items()}
    return {
        'answers': [sections[label_map[label]] for label in sorted(label_map)],
        'verdict': {'kind': kind, 'labels': [label_of[p] for p in providers_named]},
        'rationale': rationale,
        'needs_verification': needs_verification,
    }


####################
# The golden fixture — three judges, one tie, one self-vote, one excluded
# outdated judge whose re-judge failed, and errors_or_unsupported present.
####################


CHATGPT_REPORT = raw_report(
    CHATGPT_MAP,
    {
        'chatgpt': answer_section(
            'A',
            errors_or_unsupported=[finding('p99 is always 200ms', 'Stated as fact with no source.')],
            omissions=[finding('', 'Says nothing about cold starts.')],
            useful_extras=[finding('a worked example', 'The arithmetic is easy to follow.')],
            unnecessary=[finding('A long aside about GPUs', 'Does not bear on the question.')],
        ),
        'vesqor': answer_section(
            'B',
            errors_or_unsupported=[finding('queues are always FIFO', 'Not true of the scheduler described.')],
            omissions=[finding('', 'Never mentions queueing delay, which dominates at load.')],
            useful_extras=[finding('measure before tuning', 'Good discipline to state up front.')],
            unnecessary=[finding('a restatement of the question', 'Adds nothing.')],
            improvements=[
                finding('Measure p99 first', 'Say which percentile and over what window.'),
                finding('', 'Give one concrete example of a queueing delay.'),
            ],
        ),
        'gemini': answer_section(
            'C',
            errors_or_unsupported=[finding('caching removes latency', 'Overstated; it moves it.')],
            omissions=[finding('trade off', 'No mention of cache invalidation cost.')],
            useful_extras=[finding('caching helps both', 'A practical pointer.')],
            unnecessary=[finding('a second summary paragraph', 'Repeats the opening.')],
        ),
    },
    'tie',
    ['chatgpt', 'vesqor'],
    'Answer A and Answer B are close.\nA is crisper; B is more complete.',
    ['the p99 figure in Answer A', 'the FIFO claim in Answer B'],
)

VESQOR_REPORT = raw_report(
    VESQOR_MAP,
    {
        'gemini': answer_section(
            'A',
            omissions=[finding('throughput trades off', 'No numbers given.')],
            useful_extras=[finding('a short glossary', 'Helps a non-specialist reader.')],
        ),
        'chatgpt': answer_section(
            'B',
            errors_or_unsupported=[finding('batching never helps latency', 'Contradicted by its own next paragraph.')],
            unnecessary=[finding('a digression about hardware', 'Off the question.')],
        ),
        'vesqor': answer_section(
            'C',
            improvements=[finding('', 'Name the measurement window explicitly.')],
        ),
    },
    'winner',
    ['chatgpt'],
    'Answer B is the most accurate of the three.',
    ['the claim that batching never helps latency', 'whether the scheduler is really FIFO'],
)

# Gemini judged the older answer set and named itself the winner; its re-judge
# then came back malformed. Both facts have to reach the summary.
GEMINI_OUTDATED_REPORT = raw_report(
    GEMINI_MAP,
    {
        'vesqor': answer_section('A'),
        'gemini': answer_section('B'),
        'chatgpt': answer_section('C'),
    },
    'winner',
    ['gemini'],
    'Answer B reads best.',
    [],
)


def golden_tally() -> dict[str, Any]:
    """The tally for the golden fixture, from the real stage-4 module."""
    return tally.compute_tally(
        FRESH_VERSIONS,
        [
            tally.TallyJudge(
                judge='chatgpt',
                current=tally.TallyReport(
                    revision=1, judged_versions=FRESH_VERSIONS, label_map=CHATGPT_MAP, report=CHATGPT_REPORT
                ),
            ),
            tally.TallyJudge(
                judge='gemini',
                current=tally.TallyReport(
                    revision=1, judged_versions=OLD_VERSIONS, label_map=GEMINI_MAP, report=GEMINI_OUTDATED_REPORT
                ),
                latest_attempt=tally.TallyAttempt(revision=2, status='failed', error_code='malformed_report'),
            ),
            tally.TallyJudge(
                judge='vesqor',
                current=tally.TallyReport(
                    revision=1, judged_versions=FRESH_VERSIONS, label_map=VESQOR_MAP, report=VESQOR_REPORT
                ),
            ),
        ],
    )


def golden_reports() -> list[summary.SummaryReport]:
    return [
        summary.SummaryReport(judge='chatgpt', label_map=CHATGPT_MAP, report=CHATGPT_REPORT),
        summary.SummaryReport(judge='vesqor', label_map=VESQOR_MAP, report=VESQOR_REPORT),
    ]


def build_golden() -> str:
    return summary.build_narrative(golden_tally(), golden_reports())


####################
# 0 — the golden file, compared whole
####################


def test_golden_narrative_matches_the_fixture_file():
    expected = GOLDEN.read_text(encoding='utf-8')
    assert build_golden() == expected


####################
# 1 — determinism, across processes
####################


# The child imports the narrative module and NOTHING else from the app. It must
# not import this test module: that would pull in open_webui.models -> config,
# which runs `alembic upgrade head` against DATABASE_URL at import time. The
# fixture therefore travels as JSON on stdin.
# The child pins the same judge panel the parent does. `build_narrative` reads
# the panel to size its "partial summary" line, so an unpinned child would
# differ from the golden for a reason that has nothing to do with hash order —
# the very thing this test exists to detect.
SUBPROCESS_SCRIPT = """
import json, sys
from open_webui.utils import answer_compare_summary
from open_webui.utils.answer_compare_summary import SummaryReport, build_narrative
payload = json.load(sys.stdin)
answer_compare_summary.resolve_judge_ids = lambda: tuple(payload['panel'])
reports = [SummaryReport(**item) for item in payload['reports']]
sys.stdout.write(build_narrative(payload['tally'], reports))
"""


def _assemble_in_subprocess(hash_seed: str) -> bytes:
    payload = json.dumps(
        {
            'tally': golden_tally(),
            'reports': [r.model_dump() for r in golden_reports()],
            'panel': list(LEGACY_PANEL),
        },
        sort_keys=False,
    )
    env = {
        **os.environ,
        'PYTHONHASHSEED': hash_seed,
        'PYTHONPATH': str(REPO / 'backend'),
        # Belt and braces: the child imports no module that reads either of these,
        # and if that ever changes it must still never touch a real database.
        'ENABLE_DB_MIGRATIONS': 'False',
        'DATABASE_URL': 'sqlite:///' + str(pathlib.Path(tempfile.gettempdir()) / 'vq25-summary-subprocess.db'),
    }
    result = subprocess.run(
        [sys.executable, '-c', SUBPROCESS_SCRIPT],
        input=payload.encode('utf-8'),
        capture_output=True,
        env=env,
        check=True,
    )
    return result.stdout


# Several seeds, not two: string hashing collides often enough that a pair can
# agree by luck. 0/1/2 happen to give one order for a set of provider ids while
# 5/8 give another, so a mutant iterating a set survives a (0, 1) pair.
HASH_SEEDS = ('0', '1', '2', '5', '8')


def test_narrative_is_byte_identical_across_hash_seeds():
    """Two assemblies in one process share PYTHONHASHSEED and would agree even
    if the code iterated a set; separate processes with different seeds do not."""
    outputs = {seed: _assemble_in_subprocess(seed) for seed in HASH_SEEDS}
    distinct = set(outputs.values())
    assert len(distinct) == 1, f'narrative varies with PYTHONHASHSEED: {sorted(outputs)}'
    assert distinct.pop().decode('utf-8') == build_golden()


def test_the_determinism_subprocess_imports_nothing_that_touches_a_database():
    """The guard on the guard: importing the app in the child would run migrations
    against whatever DATABASE_URL it inherited."""
    assert 'test_vq25_summary' not in SUBPROCESS_SCRIPT
    assert 'open_webui.models' not in SUBPROCESS_SCRIPT
    assert 'open_webui.config' not in SUBPROCESS_SCRIPT

    probe = subprocess.run(
        [
            sys.executable,
            '-c',
            SUBPROCESS_SCRIPT
            + '\nimport sys; sys.stderr.write(repr(sorted(m for m in sys.modules if "internal.db" in m or m.endswith("open_webui.config"))))',
        ],
        input=json.dumps(
            {
                'tally': golden_tally(),
                'reports': [r.model_dump() for r in golden_reports()],
                'panel': list(LEGACY_PANEL),
            }
        ).encode(),
        capture_output=True,
        env={
            **os.environ,
            'PYTHONPATH': str(REPO / 'backend'),
            'ENABLE_DB_MIGRATIONS': 'False',
            'DATABASE_URL': 'sqlite:///' + str(pathlib.Path(tempfile.gettempdir()) / 'vq25-summary-subprocess.db'),
        },
        check=True,
    )
    assert probe.stderr.decode().strip() == '[]'


def test_narrative_is_stable_in_process_and_under_shuffled_input_keys():
    assert build_golden() == build_golden()

    def reversed_keys(node):
        if isinstance(node, dict):
            return {k: reversed_keys(node[k]) for k in reversed(list(node))}
        if isinstance(node, list):
            return [reversed_keys(item) for item in node]
        return node

    shuffled_reports = [
        summary.SummaryReport(judge=r.judge, label_map=reversed_keys(r.label_map), report=reversed_keys(r.report))
        for r in golden_reports()
    ]
    assert summary.build_narrative(reversed_keys(golden_tally()), shuffled_reports) == build_golden()


def test_narrative_contains_nothing_that_varies_between_assemblies():
    text = build_golden()
    for token in ('created_at', 'updated_at', 'revision 1', 'attempt', 'seconds', 'ms)', 'id='):
        assert token not in text, token
    # No 32-hex row id ever reaches the narrative.
    assert not any(len(word) == 32 and all(c in '0123456789abcdef' for c in word) for word in text.split())


####################
# 2 — no model call
####################


def test_summary_module_does_not_import_the_provider_client():
    """Checked on the import statements, not the prose: the module's own docstring
    says why it must not import the client, and a substring check would trip on that."""
    tree = ast.parse(
        (REPO / 'backend' / 'open_webui' / 'utils' / 'answer_compare_summary.py').read_text(encoding='utf-8')
    )

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in ('httpx', 'open_webui.utils.answer_compare_client'):
        assert not any(name == forbidden or name.startswith(f'{forbidden}.') for name in imported), forbidden
    assert not any('client' in name for name in imported), sorted(imported)


def test_building_a_summary_performs_no_http(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError('the summary must never call a provider')

    monkeypatch.setattr(provider_client, '_build_async_client', explode)
    monkeypatch.setattr(httpx.AsyncClient, 'request', explode)
    monkeypatch.setattr(httpx.Client, 'request', explode)

    assert summary.build_summary(golden_tally(), golden_reports()).narrative == build_golden()


####################
# 3 — the narrative cannot contradict the tally
####################


def test_outcome_and_agreement_sections_follow_the_tally_not_the_reports():
    """Both verdicts name ChatGPT; the tally says no majority. Section 1 must state
    the tally's outcome, and section 3 must not claim a winner that contradicts it."""
    contradicting = golden_tally()
    contradicting['verdicts'] = [
        {'judge': 'chatgpt', 'kind': 'winner', 'providers': ['chatgpt']},
        {'judge': 'vesqor', 'kind': 'winner', 'providers': ['chatgpt']},
    ]
    contradicting['outcome'] = {'kind': 'no_majority', 'provider': None}
    contradicting['votes'] = {'chatgpt': 0, 'gemini': 0, 'vesqor': 0}

    text = summary.build_narrative(contradicting, golden_reports())

    assert 'No majority among 2 valid verdicts.' in text
    assert 'Preferred answer' not in text
    # Section 3 renders from tally.verdicts, so it reports the agreement the tally
    # holds — and never a "preferred"/"winner" claim the outcome denies.
    assert 'Two judges named ChatGPT the winner.' in text
    assert 'Judges disagreed' not in text


def test_outcome_sentence_is_the_tallys_even_when_votes_disagree_with_verdicts():
    inflated = golden_tally()
    inflated['outcome'] = {'kind': 'preferred', 'provider': 'gemini'}
    inflated['votes'] = {'chatgpt': 0, 'gemini': 7, 'vesqor': 0}
    inflated['n_included'] = 9

    text = summary.build_narrative(inflated, golden_reports())
    assert 'Preferred answer: Gemini (7 of 9 judges).' in text


def test_single_included_verdict_omits_the_agreement_section():
    single = tally.compute_tally(
        FRESH_VERSIONS,
        [
            tally.TallyJudge(
                judge='chatgpt',
                current=tally.TallyReport(
                    revision=1, judged_versions=FRESH_VERSIONS, label_map=CHATGPT_MAP, report=CHATGPT_REPORT
                ),
            )
        ],
    )
    result = summary.build_summary(single, [golden_reports()[0]])

    assert summary.HEADING_AGREEMENT not in result.narrative
    assert result.partial is True
    assert 'Partial summary: 1 of 3 judges included.' in result.narrative
    # The outcome says why in words, so "no majority" beside one clear verdict
    # does not read as a bug (DECISIONS.md#014).
    assert (
        'No majority: only one judge has a valid verdict — a preferred answer requires at least two.'
    ) in result.narrative
    assert 'No majority among' not in result.narrative


def test_the_single_judge_explanation_is_only_used_for_a_single_judge():
    text = build_golden()  # two included verdicts
    assert 'No majority among 2 valid verdicts.' in text
    assert 'only one judge has a valid verdict' not in text


####################
# 4 — judge text is verbatim
####################


def test_judge_text_appears_character_for_character():
    text = build_golden()
    for fragment in (
        'Answer A and Answer B are close.\nA is crisper; B is more complete.',
        'Answer B is the most accurate of the three.',
        'Never mentions queueing delay, which dominates at load.',
        'Contradicted by its own next paragraph.',
        'the claim that batching never helps latency',
        'Say which percentile and over what window.',
    ):
        assert fragment in text, fragment


####################
# 5 — sections: 1 and 10 always, 2–9 conditional
####################


ALL_HEADINGS = (
    summary.HEADING_OUTCOME,
    summary.HEADING_VERDICTS,
    summary.HEADING_AGREEMENT,
    summary.HEADING_ERRORS,
    summary.HEADING_OMISSIONS,
    summary.HEADING_EXTRAS,
    summary.HEADING_UNNECESSARY,
    summary.HEADING_IMPROVEMENTS,
    summary.HEADING_VERIFICATION,
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
    summary,
    answer_compare_router,
)

LEGACY_PANEL = ('chatgpt', 'gemini', 'vesqor')


@pytest.fixture(autouse=True)
def legacy_judge_panel(monkeypatch):
    for module in _PANEL_SEAMS:
        monkeypatch.setattr(module, 'resolve_judge_ids', lambda: LEGACY_PANEL)
    return monkeypatch


def test_all_ten_sections_present_in_the_golden_fixture():
    text = build_golden()
    positions = [text.index(heading) for heading in ALL_HEADINGS]
    assert positions == sorted(positions), 'sections must appear in the fixed order'
    assert text.rstrip('\n').endswith(summary.CLOSING_LINE)


def test_empty_sections_are_omitted_entirely():
    bare = raw_report(
        CHATGPT_MAP,
        {p: answer_section(label) for label, p in CHATGPT_MAP.items()},
        'winner',
        ['vesqor'],
        'Short and to the point.',
        [],
    )
    bare_tally = tally.compute_tally(
        FRESH_VERSIONS,
        [
            tally.TallyJudge(
                judge='chatgpt',
                current=tally.TallyReport(
                    revision=1, judged_versions=FRESH_VERSIONS, label_map=CHATGPT_MAP, report=bare
                ),
            )
        ],
    )
    text = summary.build_narrative(
        bare_tally, [summary.SummaryReport(judge='chatgpt', label_map=CHATGPT_MAP, report=bare)]
    )

    assert summary.HEADING_OUTCOME in text
    assert summary.HEADING_VERDICTS in text
    for heading in ALL_HEADINGS[2:]:
        assert heading not in text, heading
    assert 'none' not in text.lower().replace('no majority', '').replace('no reliable', '')
    assert text.rstrip('\n').endswith(summary.CLOSING_LINE)


####################
# 6 — improvements target VESQOR through the mapped report
####################


def test_improvements_section_collects_only_the_vesqor_items():
    text = build_golden()
    section = text.split(summary.HEADING_IMPROVEMENTS, 1)[1].split(summary.HEADING_VERIFICATION, 1)[0]

    # Both maps put vesqor somewhere other than A; these two items are its own.
    assert 'Say which percentile and over what window.' in section
    assert 'Name the measurement window explicitly.' in section
    # Items recorded for other providers must not leak in.
    assert 'Does not bear on the question.' not in section
    assert 'No numbers given.' not in section


####################
# 7 — self-vote notes annotate; counts are unchanged
####################


def test_self_vote_notes_appear_without_changing_the_counts():
    text = build_golden()
    assert 'Note: ChatGPT included its own answer in a tie.' in text
    # The excluded judge's self-vote is flagged too, so summary and panel agree.
    assert 'Note: Gemini named its own answer the winner.' in text

    unflagged = golden_tally()
    unflagged['self_votes'] = []
    unflagged['self_votes_excluded'] = []
    without = summary.build_narrative(unflagged, golden_reports())

    outcome_of = lambda t: t.split(summary.HEADING_VERDICTS, 1)[0]  # noqa: E731
    assert outcome_of(without).replace('  Note: Gemini named its own answer the winner.\n', '') == outcome_of(
        text
    ).replace('  Note: Gemini named its own answer the winner.\n', '')
    assert 'No majority among 2 valid verdicts.' in without


####################
# 8 — partial
####################


def test_partial_lines_name_every_excluded_judge_in_plain_words():
    text = build_golden()
    assert 'Partial summary: 2 of 3 judges included.' in text
    assert '- Gemini: report is outdated (answers changed); latest re-judge could not be read.' in text


def test_three_included_judges_produce_no_partial_line():
    full = tally.compute_tally(
        FRESH_VERSIONS,
        [
            tally.TallyJudge(
                judge=judge_id,
                current=tally.TallyReport(
                    revision=1, judged_versions=FRESH_VERSIONS, label_map=CHATGPT_MAP, report=CHATGPT_REPORT
                ),
            )
            for judge_id in PROVIDERS
        ],
    )
    reports = [summary.SummaryReport(judge=j, label_map=CHATGPT_MAP, report=CHATGPT_REPORT) for j in PROVIDERS]
    result = summary.build_summary(full, reports)

    assert 'Partial summary' not in result.narrative
    assert result.partial is False
    assert result.included_judges == PROVIDERS


def test_an_unmappable_report_excludes_only_that_judge():
    broken = summary.SummaryReport(judge='vesqor', label_map={'A': 'chatgpt'}, report=VESQOR_REPORT)
    result = summary.build_summary(golden_tally(), [golden_reports()[0], broken])

    assert 'Answer A and Answer B are close.' in result.narrative  # chatgpt's report survived
    assert 'Answer B is the most accurate of the three.' not in result.narrative
    assert '- VESQOR: report could not be mapped to provider names.' in result.narrative
    assert result.included_judges == ['chatgpt']
    assert result.partial is True


####################
# 9–12 — the endpoint: persistence, outdated, 409, admin gate
####################

DUMMY_KEY = 'sk-vq25-summary-secret-do-not-leak'
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


@pytest.fixture
def endpoint_env(monkeypatch):
    for names in PROVIDER_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for provider_id in PROVIDERS:
        monkeypatch.setenv(PROVIDER_ENV[provider_id][0], PROVIDER_HOST[provider_id])
        monkeypatch.setenv(PROVIDER_ENV[provider_id][1], DUMMY_KEY)
        monkeypatch.setenv(PROVIDER_ENV[provider_id][2], f'{provider_id}-model')
    judge.reset_mode_cache()
    asyncio.run(_create_tables())
    return monkeypatch


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    tables = [m.__table__ for m in (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


def _user(role: str) -> UserModel:
    return UserModel(
        id=f'{role}-id', email=f'{role}@example.com', name=role, role=role, last_active_at=0, updated_at=0, created_at=0
    )


def client_as(role: str = 'admin') -> TestClient:
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


def seed_run(with_reports: bool = True) -> str:
    """A run with three complete answers and, optionally, two complete reports."""

    async def _seed():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id', admin_email='admin@example.com', form=AnswerCompareRunForm(prompt='q', reference=None)
        )
        for provider_id in PROVIDERS:
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run.id, provider=provider_id, status=STATUS_COMPLETE, text=f'{provider_id} answer'
            )
        if with_reports:
            versions = [{'provider': p, 'revision': 1} for p in PROVIDERS]
            await AnswerCompareReports.insert_next_revision(
                run_id=run.id,
                judge='chatgpt',
                status=STATUS_COMPLETE,
                label_map=CHATGPT_MAP,
                report=CHATGPT_REPORT,
                judged_versions=versions,
            )
            await AnswerCompareReports.insert_next_revision(
                run_id=run.id,
                judge='vesqor',
                status=STATUS_COMPLETE,
                label_map=VESQOR_MAP,
                report=VESQOR_REPORT,
                judged_versions=versions,
            )
        return run.id

    return asyncio.run(_seed())


def stored_summaries(run_id: str) -> list[dict]:
    async def _rows():
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            result = await db.execute(
                select(AnswerCompareSummary)
                .where(AnswerCompareSummary.run_id == run_id)
                .order_by(AnswerCompareSummary.revision)
            )
            return [
                {
                    'id': r.id,
                    'revision': r.revision,
                    'narrative': r.narrative,
                    'tally': r.tally,
                    'judged_versions': r.judged_versions,
                    'partial': r.partial,
                    'included_judges': r.included_judges,
                }
                for r in result.scalars().all()
            ]

    return asyncio.run(_rows())


def summary_url(run_id: str) -> str:
    return f'/api/v1/compare/runs/{run_id}/summary'


def test_post_appends_revisions_and_persists_what_was_computed(endpoint_env):
    run_id = seed_run()
    api = client_as()

    first = api.post(summary_url(run_id))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body['revision'] == 1
    assert body['outdated'] is False
    assert body['partial'] is True  # gemini never judged
    assert body['included_judges'] == ['chatgpt', 'vesqor']
    assert summary.CLOSING_LINE in body['narrative']

    second = api.post(summary_url(run_id)).json()
    assert second['revision'] == 2

    rows = stored_summaries(run_id)
    assert [r['revision'] for r in rows] == [1, 2]
    assert rows[0]['id'] != rows[1]['id']
    assert rows[0]['narrative'] == body['narrative']
    assert rows[0]['judged_versions'] == [{'provider': p, 'revision': 1} for p in PROVIDERS]
    assert rows[0]['included_judges'] == ['chatgpt', 'vesqor']
    assert rows[0]['partial'] is True
    assert rows[0]['tally']['included_reports'] == [
        {'judge': 'chatgpt', 'revision': 1},
        {'judge': 'vesqor', 'revision': 1},
    ]


def test_get_carries_the_summary_and_its_revision_count(endpoint_env):
    run_id = seed_run()
    api = client_as()
    api.post(summary_url(run_id))
    api.post(summary_url(run_id))

    payload = api.get(f'/api/v1/compare/runs/{run_id}').json()['summary']
    assert payload['revisions'] == 2
    assert payload['current']['revision'] == 2
    assert payload['current']['outdated'] is False


def test_outdated_when_the_answers_move(endpoint_env):
    run_id = seed_run()
    api = client_as()
    api.post(summary_url(run_id))

    asyncio.run(
        AnswerCompareAnswers.insert_next_revision(
            run_id=run_id, provider='vesqor', status=STATUS_COMPLETE, text='regenerated'
        )
    )
    payload = api.get(f'/api/v1/compare/runs/{run_id}').json()['summary']
    assert payload['current']['outdated'] is True


def test_outdated_when_only_the_reports_move(endpoint_env):
    """The second dimension: the answer set is untouched, but a judge re-judged."""
    run_id = seed_run()
    api = client_as()
    api.post(summary_url(run_id))
    assert api.get(f'/api/v1/compare/runs/{run_id}').json()['summary']['current']['outdated'] is False

    versions = [{'provider': p, 'revision': 1} for p in PROVIDERS]
    asyncio.run(
        AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge='chatgpt',
            status=STATUS_COMPLETE,
            label_map=CHATGPT_MAP,
            report=CHATGPT_REPORT,
            judged_versions=versions,
        )
    )

    payload = api.get(f'/api/v1/compare/runs/{run_id}').json()['summary']
    assert payload['current']['outdated'] is True
    # And rebuilding clears it.
    api.post(summary_url(run_id))
    assert api.get(f'/api/v1/compare/runs/{run_id}').json()['summary']['current']['outdated'] is False


def test_outdated_is_false_when_a_reordered_list_says_the_same_thing(endpoint_env):
    """Sets of normalised tuples, not lists of dicts: order must not read as change."""
    run_id = seed_run()
    client_as().post(summary_url(run_id))

    async def _reorder():
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            result = await db.execute(select(AnswerCompareSummary).where(AnswerCompareSummary.run_id == run_id))
            row = result.scalars().first()
            row.judged_versions = list(reversed(row.judged_versions))
            row.tally = {**row.tally, 'included_reports': list(reversed(row.tally['included_reports']))}
            await db.commit()

    asyncio.run(_reorder())
    payload = client_as().get(f'/api/v1/compare/runs/{run_id}').json()['summary']
    assert payload['current']['outdated'] is False


def test_409_with_no_included_verdicts_writes_nothing(endpoint_env):
    run_id = seed_run(with_reports=False)
    response = client_as().post(summary_url(run_id))

    assert response.status_code == 409
    detail = response.json()['detail']
    assert detail['code'] == 'no_verdicts_to_summarise'
    assert [item['reason'] for item in detail['excluded']] == ['no_report', 'no_report', 'no_report']
    assert stored_summaries(run_id) == []


def test_404_and_401(endpoint_env):
    run_id = seed_run()
    assert client_as().post(summary_url('nope')).status_code == 404
    assert client_as().post(summary_url('nope')).json()['detail']['code'] == 'run_not_found'
    assert client_as('user').post(summary_url(run_id)).status_code == 401


def test_no_key_material_in_the_summary(endpoint_env):
    run_id = seed_run()
    api = client_as()
    assert DUMMY_KEY not in api.post(summary_url(run_id)).text
    assert DUMMY_KEY not in api.get(f'/api/v1/compare/runs/{run_id}').text
    assert DUMMY_KEY not in json.dumps(stored_summaries(run_id))
