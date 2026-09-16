"""Fixtures for the adjudication regression suite.

The builders here exist so a test can state the *situation* it is about — "B
fabricates a test result", "the corpus marks nothing CRITICAL" — without
restating thirty lines of model output each time. Everything a test does not
name is filled with something deliberately neutral, so a failure points at the
thing the test changed.
"""

import copy
import json
import os
from typing import Any, Optional

import pytest

from open_webui.utils import answer_compare_adjudication as adjudication

# Where the end-to-end fixtures live. Point this at a directory of real reports
# to run the suite against them:
#     ANSWER_COMPARE_FIXTURE_DIR=/path/to/compare pytest ...
# The directory is expected to hold candidate_a.txt / candidate_b.txt /
# candidate_c.txt and reference.txt (or .md). When it is unset the committed
# fixtures under ``fixtures/`` are used, so the suite is self-contained.
FIXTURE_ENV = 'ANSWER_COMPARE_FIXTURE_DIR'
DEFAULT_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')

CANDIDATE_FILES = {'A': 'candidate_a', 'B': 'candidate_b', 'C': 'candidate_c'}
REFERENCE_FILE = 'reference'
REQUIREMENTS_FILE = 'requirements'
_EXTENSIONS = ('.txt', '.md')


def _read_first(directory: str, stem: str) -> Optional[str]:
    for extension in _EXTENSIONS:
        path = os.path.join(directory, stem + extension)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as handle:
                return handle.read()
    return None


@pytest.fixture(scope='session')
def fixture_dir() -> str:
    return os.environ.get(FIXTURE_ENV, DEFAULT_FIXTURE_DIR)


@pytest.fixture(scope='session')
def corpus(fixture_dir: str) -> dict[str, Any]:
    """The three candidate reports plus the authoritative material.

    Missing candidate files are not silently skipped: a suite that quietly
    adjudicated two reports because the third file was misnamed would prove
    nothing about the three-candidate path.
    """
    if not os.path.isdir(fixture_dir):
        pytest.skip(f'fixture directory {fixture_dir} does not exist')

    candidates: dict[str, str] = {}
    for label, stem in CANDIDATE_FILES.items():
        text = _read_first(fixture_dir, stem)
        if text is None:
            pytest.skip(f'missing candidate fixture {stem}.txt in {fixture_dir}')
        candidates[label] = text

    return {
        'candidates': candidates,
        'reference': _read_first(fixture_dir, REFERENCE_FILE) or '',
        'requirements': (_read_first(fixture_dir, REQUIREMENTS_FILE) or 'Report on the deployment.').strip(),
    }


####################
# Request builders
####################


# A small corpus that exists so the default builders have real evidence ids to
# cite. Tests that care about the corpus pass their own; tests that do not still
# get E1/E2/E3, because citing an id that was never supplied is (correctly) a
# validation error and would otherwise mask what the test is actually about.
DEFAULT_REFERENCE = (
    'CRITICAL: The migration ran at 02:14 UTC.\n\nHIGH: Two retries were needed.\n\nThe cache was cold at start.'
)


def make_request(
    candidates: Optional[dict[str, str]] = None,
    reference: str = DEFAULT_REFERENCE,
    requirements: str = 'Summarise what the deployment log shows.',
    acceptance: Optional[str] = None,
) -> adjudication.AdjudicationRequest:
    return adjudication.AdjudicationRequest(
        task_requirements=requirements,
        acceptance_criteria=acceptance,
        evidence=adjudication.split_reference(reference),
        candidates=candidates or {'A': 'answer a', 'B': 'answer b', 'C': 'answer c'},
    )


####################
# Model-output builders
####################


def categories(**overrides: float) -> dict[str, float]:
    """A complete category map: named categories take the given value, the rest 0.

    Tie-break tests need two candidates whose totals land within a point while
    differing sharply on one category, which is only expressible by stating
    every number. ``candidate(score=...)`` fills gaps proportionally, so a
    partial dict there would quietly reintroduce the spread being tested away.
    """
    unknown = sorted(set(overrides) - set(adjudication.CATEGORY_KEYS))
    if unknown:
        raise AssertionError(f'unknown categories in test fixture: {unknown}')
    return {key: float(overrides.get(key, 0.0)) for key in adjudication.CATEGORY_KEYS}


def claim(
    text: str = 'the deploy succeeded',
    classification: str = adjudication.CLAIM_VERIFIED,
    material: bool = True,
    evidence_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        'claim': text,
        'passage': text,
        'classification': classification,
        'material': material,
        'evidence_ids': evidence_ids
        if evidence_ids is not None
        else (['E1'] if classification == adjudication.CLAIM_VERIFIED else []),
        'note': '',
    }


def penalty(
    kind: str = adjudication.PENALTY_FACTUAL_ERROR,
    severity: str = adjudication.SEVERITY_MODERATE,
    passage: str = 'a wrong sentence',
    note: str = 'contradicts the log',
) -> dict[str, Any]:
    return {
        'kind': kind,
        'severity': severity,
        'passage': passage,
        'note': note,
        'evidence_ids': [],
    }


def finding(note: str = 'a note', passage: str = 'a passage') -> dict[str, Any]:
    return {'passage': passage, 'note': note}


def candidate(
    label: str,
    score: float = 7.0,
    category_scores: Optional[dict[str, float]] = None,
    claims: Optional[list[dict]] = None,
    penalties: Optional[list[dict]] = None,
    covered: Optional[list[str]] = None,
    missed: Optional[list[str]] = None,
    **findings: list,
) -> dict[str, Any]:
    """One candidate's findings.

    ``score`` is a convenience: it fills every category at that fraction of its
    own maximum, so a test can say "A is a 9/10 answer, B is a 6/10 answer" and
    get a sensible spread without writing ten numbers.
    """
    if category_scores is None:
        category_scores = {
            key: round(adjudication.CATEGORY_WEIGHTS[key] * score / 10.0, 1) for key in adjudication.CATEGORY_KEYS
        }
    else:
        category_scores = {
            key: category_scores.get(key, round(adjudication.CATEGORY_WEIGHTS[key] * score / 10.0, 1))
            for key in adjudication.CATEGORY_KEYS
        }

    return {
        'label': label,
        'claims': claims if claims is not None else [claim()],
        'category_scores': category_scores,
        'penalties': penalties or [],
        'coverage': {
            'covered_evidence_ids': covered if covered is not None else [],
            'missed_evidence_ids': missed if missed is not None else [],
        },
        'strengths': findings.get('strengths', [finding('clear')]),
        'critical_errors': findings.get('critical_errors', []),
        'critical_omissions': findings.get('critical_omissions', []),
        'improvements': findings.get('improvements', [finding('be specific')]),
        'unnecessary': findings.get('unnecessary', []),
        'useful_extras': findings.get('useful_extras', []),
    }


def adjudication_output(
    candidates: list[dict[str, Any]],
    declared_winner: str = 'A',
    confidence: str = adjudication.CONFIDENCE_MEDIUM,
    pairwise: Optional[list[dict]] = None,
    category_winners: Optional[dict[str, list[str]]] = None,
    **overrides: Any,
) -> dict[str, Any]:
    labels = [item['label'] for item in candidates]
    payload = {
        'evidence_matrix': [],
        'candidates': candidates,
        'category_winners': category_winners
        if category_winners is not None
        else {key: [labels[0]] for key in adjudication.CATEGORY_KEYS},
        'pairwise': pairwise if pairwise is not None else [],
        'declared_winner': declared_winner,
        'confidence': confidence,
        'decisive_reasons': ['A is better supported.'],
        'winner_gap_analysis': ['A still hedges on the timeline.'],
        'loser_recovery_analysis': [{'label': label, 'actions': ['cite the log']} for label in labels[1:]],
        'final_adjudication': 'A wins on evidence.',
        'unresolved_uncertainty': [],
        'needs_verification': [],
    }
    payload.update(overrides)
    return payload


def as_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def parse(
    payload: dict[str, Any],
    request: Optional[adjudication.AdjudicationRequest] = None,
    budget: Optional[adjudication.BudgetOutcome] = None,
) -> adjudication.NormalizedAdjudication:
    """Validate and normalise in one step — the whole server-side path.

    With no explicit request the candidate set is taken from the payload, so a
    two-candidate test does not fail on a three-candidate request it never
    asked for.
    """
    if request is None:
        request = make_request(candidates={item['label']: f'answer {item["label"]}' for item in payload['candidates']})
    raw = adjudication.parse_adjudication(as_json(payload), request.labels, [item.id for item in request.evidence])
    return adjudication.normalize(raw, request, budget or adjudication.BudgetOutcome(fits=True))


@pytest.fixture
def clone():
    return copy.deepcopy
