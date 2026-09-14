"""VQ-25: the summary narrative — assembled in code, never by a model.

Pure functions, no database and **no provider calls of any kind**: this module
must never import ``answer_compare_client``, and a test asserts that at source
level. The plan requires the summary to be deterministic text built from
evidence already gathered, not a second generation that could invent something
the judges never said.

Input is the tally from ``answer_compare_tally`` plus, per included judge, the
stored report and its label map. The verdict-bearing sections render **from the
tally object**; from the reports comes only authored text — ``rationale``,
``note``, ``passage``, ``needs_verification`` — inserted verbatim. That split is
what keeps the document from stating "no majority" in one section and "two
judges named VESQOR the winner" in another.

Determinism: nothing here iterates a set, and the narrative carries no
timestamps, row ids, durations or attempt counters. Every list is walked in the
fixed provider/judge order or in the order the tally already fixed.
"""

import logging
from typing import Any, Optional

from open_webui.utils.answer_compare_judge import UnmappedLabel, map_report
from open_webui.utils.answer_compare_providers import PROVIDER_IDS
from pydantic import BaseModel

log = logging.getLogger(__name__)

PROVIDER_LABELS: dict[str, str] = {'chatgpt': 'ChatGPT', 'gemini': 'Gemini', 'vesqor': 'VESQOR'}

# The owner's own system: section 8 collects improvements recorded for it.
OWNER_PROVIDER = 'vesqor'

HEADING_OUTCOME = 'OUTCOME'
HEADING_VERDICTS = 'VERDICTS'
HEADING_AGREEMENT = 'AGREEMENTS AND DISAGREEMENTS'
HEADING_ERRORS = 'ERRORS AND UNSUPPORTED CLAIMS'
HEADING_OMISSIONS = 'WHAT EACH ANSWER MISSES'
HEADING_EXTRAS = 'USEFUL EXTRAS'
HEADING_UNNECESSARY = 'UNNECESSARY CONTENT'
HEADING_IMPROVEMENTS = 'IMPROVEMENTS FOR THE VESQOR ANSWER'
HEADING_VERIFICATION = 'CLAIMS THAT NEED VERIFICATION'

CLOSING_LINE = 'Agreement between judges is not evidence of correctness.'

# Exclusion reasons in plain words. Mirrors the page's copy deliberately: the
# summary is copied out of the page, and the two must not disagree.
EXCLUSION_WORDS: dict[str, str] = {
    'no_report': 'not judged',
    'outdated': 'report is outdated (answers changed)',
    'failed': 'report failed',
    'malformed': 'report could not be read',
    'not_configured': 'requires configuration',
    'unmappable': 'report could not be mapped to provider names',
}

LATEST_ATTEMPT_WORDS: dict[str, str] = {
    'failed': 'latest re-judge failed',
    'malformed': 'latest re-judge could not be read',
}

COUNT_WORDS: dict[int, str] = {2: 'Two', 3: 'Three'}


class SummaryReport(BaseModel):
    """One included judge's stored report, as the summary needs it."""

    judge: str
    label_map: dict[str, str]
    report: dict[str, Any]


class SummaryResult(BaseModel):
    """The narrative plus everything the endpoint persists."""

    narrative: str
    tally: dict[str, Any]
    judged_versions: list[dict[str, Any]]
    partial: bool
    included_judges: list[str]


def _name(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def _labels_by_provider(label_map: dict[str, str]) -> dict[str, str]:
    """provider -> the anonymous label it had for this judge."""
    return {provider: label for label, provider in label_map.items()}


def _named_with_label(provider: str, label_map: dict[str, str]) -> str:
    """ "VESQOR (was B)" — the label is what makes the judge's own words readable."""
    label = _labels_by_provider(label_map).get(provider)
    return f'{_name(provider)} (was {label})' if label else _name(provider)


def _self_vote_note(judge: str, kind: str) -> str:
    if kind == 'tie':
        return f'Note: {_name(judge)} included its own answer in a tie.'
    return f'Note: {_name(judge)} named its own answer the winner.'


def _map_included_reports(reports: list[SummaryReport]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Map each included report, dropping — never crashing on — one that cannot be mapped.

    Returns (mapped by judge, judges that could not be mapped). One judge's bad
    row never removes the others; that rule has held since stage 2.
    """
    mapped: dict[str, dict[str, Any]] = {}
    unmappable: list[str] = []
    for entry in reports:
        try:
            mapped[entry.judge] = map_report(entry.report, entry.label_map)
        except UnmappedLabel as err:
            log.error(
                'summary: report of judge %s names label %r that its label map does not contain; excluding it',
                entry.judge,
                err.label,
            )
            unmappable.append(entry.judge)
    return mapped, unmappable


def _section_outcome(tally: dict[str, Any], extra_excluded: list[str]) -> list[str]:
    """Always present, rendered from the tally — never recomputed from reports."""
    outcome = tally['outcome']
    lines = [HEADING_OUTCOME, '']

    if outcome['kind'] == 'preferred' and outcome['provider']:
        provider = outcome['provider']
        lines.append(
            f'Preferred answer: {_name(provider)} ({tally["votes"].get(provider, 0)} of {tally["n_included"]} judges).'
        )
    elif tally['n_included'] == 1:
        # Otherwise "no majority" with a single clear verdict reads as a bug.
        lines.append('No majority: only one judge has a valid verdict — a preferred answer requires at least two.')
    else:
        lines.append(f'No majority among {tally["n_included"]} valid verdicts.')

    excluded = list(tally['excluded']) + [{'judge': judge, 'reason': 'unmappable'} for judge in extra_excluded]
    included_count = tally['n_included'] - len(extra_excluded)
    partial = included_count < len(PROVIDER_IDS)

    if partial:
        lines.append('')
        lines.append(f'Partial summary: {included_count} of {len(PROVIDER_IDS)} judges included.')
        self_voted = {item['judge']: item['kind'] for item in tally['self_votes_excluded']}
        by_judge = {item['judge']: item for item in excluded}
        for judge in PROVIDER_IDS:
            item = by_judge.get(judge)
            if item is None:
                continue
            words = EXCLUSION_WORDS.get(item['reason'], item['reason'])
            line = f'- {_name(judge)}: {words}'
            attempt = item.get('latest_attempt')
            if attempt:
                line += f'; {LATEST_ATTEMPT_WORDS.get(attempt, attempt)}'
            lines.append(line + '.')
            # Every self-vote is flagged, including one on an excluded verdict:
            # the panel shows those too, and the two must not disagree.
            if judge in self_voted:
                lines.append(f'  {_self_vote_note(judge, self_voted[judge])}')

    return lines


def _verdict_clause(verdict: dict[str, Any], label_map: Optional[dict[str, str]]) -> str:
    kind = verdict['kind']
    providers = verdict['providers']
    if kind == 'winner' and providers:
        return f'winner — {_named_with_label(providers[0], label_map or {})}'
    if kind == 'tie':
        named = ', '.join(_named_with_label(p, label_map or {}) for p in providers)
        return f'tie — {named}'
    return 'no reliable winner'


def _section_verdicts(
    tally: dict[str, Any], mapped: dict[str, dict[str, Any]], label_maps: dict[str, dict[str, str]]
) -> list[str]:
    """One block per included judge: the verdict from the tally, the rationale verbatim."""
    self_voted = {item['judge']: item['kind'] for item in tally['self_votes']}
    blocks: list[str] = []

    for verdict in tally['verdicts']:
        judge = verdict['judge']
        if judge not in mapped:
            continue
        block = [f'{_name(judge)}: {_verdict_clause(verdict, label_maps.get(judge))}.']
        rationale = mapped[judge].get('rationale')
        if isinstance(rationale, str) and rationale.strip():
            block.append(rationale)
        if judge in self_voted:
            block.append(_self_vote_note(judge, self_voted[judge]))
        blocks.append('\n'.join(block))

    if not blocks:
        return []
    return [HEADING_VERDICTS, '', '\n\n'.join(blocks)]


def _section_agreement(tally: dict[str, Any], excluded_judges: list[str]) -> list[str]:
    """Facts about verdicts, computed from `tally.verdicts` — never from the reports.

    Omitted entirely with one included verdict: one judge can neither agree nor
    disagree. Never phrased as correctness, likelihood or confidence.
    """
    verdicts = [v for v in tally['verdicts'] if v['judge'] not in excluded_judges]
    if len(verdicts) < 2:
        return []

    lines: list[str] = []

    for provider in PROVIDER_IDS:
        agreeing = [v for v in verdicts if v['kind'] == 'winner' and v['providers'] == [provider]]
        if len(agreeing) > 1:
            word = COUNT_WORDS.get(len(agreeing), str(len(agreeing)))
            lines.append(f'{word} judges named {_name(provider)} the winner.')

    # len only — never iterate: this set's order is not defined, and the
    # narrative must be byte-identical across processes.
    shapes = {(v['kind'], tuple(v['providers'])) for v in verdicts}
    if len(shapes) > 1:
        clauses = []
        for verdict in verdicts:
            judge = _name(verdict['judge'])
            if verdict['kind'] == 'winner' and verdict['providers']:
                clauses.append(f'{judge} named {_name(verdict["providers"][0])}')
            elif verdict['kind'] == 'tie':
                named = ', '.join(_name(p) for p in verdict['providers'])
                clauses.append(f'{judge} judged a tie between {named}')
            else:
                clauses.append(f'{judge} found no reliable winner')
        lines.append('Judges disagreed: ' + '; '.join(clauses) + '.')

    if not lines:
        return []
    return [HEADING_AGREEMENT, '', '\n'.join(lines)]


def _finding_line(judge: str, item: dict[str, Any]) -> str:
    """`Gemini: "…passage…" — …note….` Both fields verbatim."""
    passage = item.get('passage') or ''
    note = item.get('note') or ''
    if passage:
        return f'{_name(judge)}: "{passage}" — {note}'
    return f'{_name(judge)}: {note}'


def _section_findings(heading: str, field: str, mapped: dict[str, dict[str, Any]], judges: list[str]) -> list[str]:
    """Per provider, every item of one field from every included report, verbatim."""
    blocks: list[str] = []
    for provider in PROVIDER_IDS:
        lines: list[str] = []
        for judge in judges:
            section = mapped.get(judge, {}).get('answers', {}).get(provider)
            if not isinstance(section, dict):
                continue
            for item in section.get(field) or []:
                lines.append(_finding_line(judge, item))
        if lines:
            # "ChatGPT's answer:" rather than "ChatGPT:", which would collide
            # with the judge attribution on the very next line.
            blocks.append('\n'.join([f"{_name(provider)}'s answer:"] + lines))

    if not blocks:
        return []
    return [heading, '', '\n\n'.join(blocks)]


def _section_improvements(mapped: dict[str, dict[str, Any]], judges: list[str]) -> list[str]:
    """The improvements recorded for the owner's own answer, resolved through the
    mapped report — never by assuming which anonymous label it had."""
    lines: list[str] = []
    for judge in judges:
        section = mapped.get(judge, {}).get('answers', {}).get(OWNER_PROVIDER)
        if not isinstance(section, dict):
            continue
        for item in section.get('improvements') or []:
            lines.append(_finding_line(judge, item))

    if not lines:
        return []
    return [HEADING_IMPROVEMENTS, '', '\n'.join(lines)]


def _section_verification(mapped: dict[str, dict[str, Any]], judges: list[str]) -> list[str]:
    """The union across included reports, in judge order then report order.

    No de-duplication: two judges raising the same claim is itself information.
    """
    lines: list[str] = []
    for judge in judges:
        for claim in mapped.get(judge, {}).get('needs_verification') or []:
            if isinstance(claim, str) and claim.strip():
                lines.append(f'{_name(judge)}: {claim}')

    if not lines:
        return []
    return [HEADING_VERIFICATION, '', '\n'.join(lines)]


def build_narrative(tally: dict[str, Any], reports: list[SummaryReport]) -> str:
    """The whole narrative. Sections in fixed order; an empty one is omitted."""
    mapped, unmappable = _map_included_reports(reports)
    label_maps = {entry.judge: entry.label_map for entry in reports}
    judges = [judge for judge in PROVIDER_IDS if judge in mapped]

    sections: list[list[str]] = [
        _section_outcome(tally, unmappable),
        _section_verdicts(tally, mapped, label_maps),
        _section_agreement(tally, unmappable),
        _section_findings(HEADING_ERRORS, 'errors_or_unsupported', mapped, judges),
        _section_findings(HEADING_OMISSIONS, 'omissions', mapped, judges),
        _section_findings(HEADING_EXTRAS, 'useful_extras', mapped, judges),
        _section_findings(HEADING_UNNECESSARY, 'unnecessary', mapped, judges),
        _section_improvements(mapped, judges),
        _section_verification(mapped, judges),
    ]

    rendered = ['\n'.join(section) for section in sections if section]
    rendered.append(CLOSING_LINE)
    return '\n\n'.join(rendered) + '\n'


def build_summary(tally: dict[str, Any], reports: list[SummaryReport]) -> SummaryResult:
    """The narrative plus the values the endpoint persists."""
    _, unmappable = _map_included_reports(reports)
    included = [judge for judge in tally['included_judges'] if judge not in unmappable]
    return SummaryResult(
        narrative=build_narrative(tally, reports),
        tally=tally,
        judged_versions=tally['current_versions'],
        partial=len(included) < len(PROVIDER_IDS),
        included_judges=included,
    )
