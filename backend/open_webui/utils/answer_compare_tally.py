"""VQ-25: the tally — computed in code from validated verdicts, never by a model.

Pure functions, no database. The caller hands in the run's current answer
versions and, per judge, the current (highest complete) report plus the latest
attempt; this module decides which verdicts count and counts them.

A preferred answer additionally requires at least two included verdicts (owner
decision, DECISIONS.md#014): a strict majority of one is not a preference.

Which verdicts count (owner decision, DECISIONS.md#012): a judge's verdict is
included iff its current report exists, is complete (so it passed validation) and
is **fresh** — its ``judged_versions`` equals the run's current versions as a
set. Everything else is excluded with a visible, deterministic reason.

Labels are resolved through ``map_report`` from the judge core — the one server
implementation of that transform, so the page and this tally cannot disagree.

Agreement between judges is not evidence of correctness. This module exposes
counts and kinds only: there is no confidence or consensus figure, and nothing
here should be read as one.

The judge panel is the judge-capable providers (``resolve_judge_ids()``), not
every compared provider: a generator-only provider is never part of the
denominator, so its absence never makes an otherwise-complete tally read as
partial. Votes, by contrast, are per ANSWER provider — every compared provider
can win, tie, or be named unreliable, whether or not it can judge.
"""

import logging
from typing import Any, Optional

from open_webui.utils.answer_compare_judge import (
    VERDICT_NO_RELIABLE_WINNER,
    VERDICT_TIE,
    VERDICT_WINNER,
    UnmappedLabel,
    map_report,
)
from open_webui.utils.answer_compare_providers import PROVIDER_IDS, resolve_judge_ids
from pydantic import BaseModel

log = logging.getLogger(__name__)

REASON_NO_REPORT = 'no_report'
REASON_OUTDATED = 'outdated'
REASON_FAILED = 'failed'
REASON_MALFORMED = 'malformed'
REASON_NOT_CONFIGURED = 'not_configured'
# A stored complete report whose labels its own map cannot resolve — a server
# invariant violation the serializer also answers with mapped: null. Not in the
# spec's list because it is practically unreachable after validation; it still
# must exclude rather than crash the whole tally.
REASON_UNMAPPABLE = 'unmappable'

OUTCOME_PREFERRED = 'preferred'
OUTCOME_NO_MAJORITY = 'no_majority'
OUTCOME_NO_VALID_VERDICTS = 'no_valid_verdicts'

MALFORMED_REPORT_CODE = 'malformed_report'

# A preferred answer needs at least this many included verdicts, on top of the
# strict majority. With one judge the majority test is vacuously true and the
# result would read as agreement where there was only ever one opinion.
MIN_JUDGES_FOR_PREFERRED = 2


class TallyReport(BaseModel):
    """A judge's current (highest complete) report, as much of it as the tally needs."""

    revision: int
    judged_versions: list[dict[str, Any]]
    label_map: dict[str, str]
    report: dict[str, Any]


class TallyAttempt(BaseModel):
    """The judge's latest attempt of any status, after the read-time stale rule."""

    revision: int
    status: str
    error_code: Optional[str] = None


class TallyJudge(BaseModel):
    judge: str
    configured: bool = True
    current: Optional[TallyReport] = None
    latest_attempt: Optional[TallyAttempt] = None


def versions_set(versions: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {(item['provider'], int(item['revision'])) for item in versions}


def is_fresh(report: TallyReport, current_versions: list[dict[str, Any]]) -> bool:
    """Fresh iff the report judged exactly the run's current answer set."""
    return versions_set(report.judged_versions) == versions_set(current_versions)


def _attempt_failure_reason(attempt: Optional[TallyAttempt]) -> Optional[str]:
    if attempt is None or attempt.status != 'failed':
        return None
    return REASON_MALFORMED if attempt.error_code == MALFORMED_REPORT_CODE else REASON_FAILED


def exclusion_reason(entry: TallyJudge, current_versions: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """None when the judge's verdict is included; otherwise the excluded entry.

    Deterministic precedence: a complete-but-stale report is ``outdated`` even
    when the retry that followed it failed — that failure rides along as
    ``latest_attempt`` on the same entry, because the admin needs both facts.
    With no complete report at all, a failed attempt outranks the configuration
    state: the attempt is a fact about this run, configuration is about future
    ones (the same reasoning that keeps a fresh report of a now-unconfigured
    judge included).
    """
    if entry.current is not None:
        if is_fresh(entry.current, current_versions):
            return None
        excluded: dict[str, Any] = {'judge': entry.judge, 'reason': REASON_OUTDATED}
        newer_failure = (
            _attempt_failure_reason(entry.latest_attempt)
            if entry.latest_attempt is not None and entry.latest_attempt.revision > entry.current.revision
            else None
        )
        if newer_failure is not None:
            excluded['latest_attempt'] = newer_failure
        return excluded

    failure = _attempt_failure_reason(entry.latest_attempt)
    if failure is not None:
        return {'judge': entry.judge, 'reason': failure}
    if not entry.configured:
        return {'judge': entry.judge, 'reason': REASON_NOT_CONFIGURED}
    return {'judge': entry.judge, 'reason': REASON_NO_REPORT}


def _mapped_verdict(report: TallyReport) -> Optional[dict[str, Any]]:
    """The verdict with providers resolved, or None when the stored report cannot be mapped."""
    try:
        mapped = map_report(report.report, report.label_map)
    except UnmappedLabel as err:
        log.error('tally: report revision %s names label %r not in its label map', report.revision, err.label)
        return None
    return {'kind': mapped['verdict']['kind'], 'providers': list(mapped['verdict']['providers'])}


def compute_tally(current_versions: list[dict[str, Any]], judges: list[TallyJudge]) -> dict[str, Any]:
    """The tally, in the shape stage 5 persists. Every list is in the fixed provider order."""
    # A JUDGES walk, not an answers one: only judge-capable providers are ever
    # tallied as judges, regardless of what the caller passed in — this is the
    # one place that decides who counts, so a provider that cannot judge (e.g.
    # a generator-only door) can never appear here even by a caller's mistake.
    judge_ids = resolve_judge_ids()
    by_judge = {entry.judge: entry for entry in judges}
    ordered = [by_judge[judge] for judge in judge_ids if judge in by_judge]

    included_reports: list[dict[str, Any]] = []
    included_judges: list[str] = []
    excluded: list[dict[str, Any]] = []
    verdicts: list[dict[str, Any]] = []
    self_votes: list[dict[str, Any]] = []
    self_votes_excluded: list[dict[str, Any]] = []
    ties: list[dict[str, Any]] = []
    inconclusive: list[str] = []
    votes: dict[str, int] = {provider: 0 for provider in PROVIDER_IDS}

    for entry in ordered:
        reason = exclusion_reason(entry, current_versions)

        if reason is not None:
            excluded.append(reason)
            # An outdated self-vote is still visible as history; it touches no count.
            if entry.current is not None:
                verdict = _mapped_verdict(entry.current)
                if verdict is not None and entry.judge in verdict['providers']:
                    self_votes_excluded.append({'judge': entry.judge, 'kind': verdict['kind']})
            continue

        assert entry.current is not None  # exclusion_reason returned None only with a fresh current report
        verdict = _mapped_verdict(entry.current)
        if verdict is None:
            excluded.append({'judge': entry.judge, 'reason': REASON_UNMAPPABLE})
            continue
        included_reports.append({'judge': entry.judge, 'revision': entry.current.revision})
        included_judges.append(entry.judge)
        verdicts.append({'judge': entry.judge, **verdict})

        kind = verdict['kind']
        providers = verdict['providers']
        if kind == VERDICT_WINNER and len(providers) == 1:
            votes[providers[0]] += 1
        elif kind == VERDICT_TIE:
            ties.append({'judge': entry.judge, 'providers': providers})
        elif kind == VERDICT_NO_RELIABLE_WINNER:
            inconclusive.append(entry.judge)

        # Annotation only. The vote above was already counted like any other.
        if entry.judge in providers:
            self_votes.append({'judge': entry.judge, 'kind': kind})

    n_included = len(included_judges)
    # A preferred answer needs a strict majority AND at least two included judges
    # (owner decision, DECISIONS.md#014): one voter is arithmetically a majority
    # of one, but substantively a single opinion, not a preference.
    preferred = (
        next((p for p in PROVIDER_IDS if votes[p] * 2 > n_included), None)
        if n_included >= MIN_JUDGES_FOR_PREFERRED
        else None
    )
    if n_included == 0:
        outcome = {'kind': OUTCOME_NO_VALID_VERDICTS, 'provider': None}
    elif preferred is not None:
        outcome = {'kind': OUTCOME_PREFERRED, 'provider': preferred}
    else:
        outcome = {'kind': OUTCOME_NO_MAJORITY, 'provider': None}

    return {
        'current_versions': [
            {'provider': p, 'revision': r}
            for p, r in sorted(versions_set(current_versions), key=lambda pr: PROVIDER_IDS.index(pr[0]))
        ],
        'included_reports': included_reports,
        'included_judges': included_judges,
        'excluded': excluded,
        'partial': n_included < len(judge_ids),
        'verdicts': verdicts,
        'votes': votes,
        'n_included': n_included,
        'outcome': outcome,
        'ties': ties,
        'inconclusive': inconclusive,
        'self_votes': self_votes,
        'self_votes_excluded': self_votes_excluded,
    }
