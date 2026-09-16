"""VQ-25: one judge — blinding, the adjudication call, and the transport ladder.

This module owns *how* a judge is called: which answers it sees, under which
anonymous labels, in which order, and how the request degrades across providers
that support different structured-output features. It does not own *what* the
judge is asked or how the answer is scored — that is
``answer_compare_adjudication``, the canonical engine, and every rubric, prompt,
schema, scoring and winner decision on the Compare screen comes from there. The
split is deliberate: there is exactly one adjudication specification, and adding
a provider or a button can never quietly fork it.

A judge is one of the configured providers, called with its own triple in one
stateless request. It receives the task, the authoritative evidence corpus and
every complete answer — including the one it wrote — under anonymous labels in a
fresh random order, and returns one structured adjudication. It is never told
which answer is its own, and never told that one might be: the silence is the
blinding.
"""

import json
import logging
import random
from typing import Any, Awaitable, Callable, Optional

from open_webui.utils import answer_compare_adjudication as adjudication
from open_webui.utils import answer_compare_client as client
from open_webui.utils.answer_compare_adjudication import (
    ADJUDICATION_ENGINE_VERSION,
    ADJUDICATION_RUBRIC_VERSION,
    VERDICT_KINDS,
    VERDICT_NO_RELIABLE_WINNER,
    VERDICT_TIE,
    VERDICT_WINNER,
    AdjudicationRequest,
    EvidenceTooLarge,
    MalformedAdjudication,
    NormalizedAdjudication,
)
from open_webui.utils.answer_compare_providers import GENERATOR_IDS
from pydantic import BaseModel

log = logging.getLogger(__name__)

LABELS: tuple[str, ...] = ('A', 'B', 'C')

# Re-exported so the tally and the summary keep importing their vocabulary from
# one place; the definitions live in the adjudication engine.
__all_verdicts__ = VERDICT_KINDS

# The finding buckets the summary narrative renders, in its order. They are a
# projection of the canonical result, not a second set of judgements.
FINDING_FIELDS: tuple[str, ...] = (
    'strengths',
    'errors_or_unsupported',
    'omissions',
    'useful_extras',
    'unnecessary',
    'improvements',
)

# Structured-output ladder, tried in order. Any 400 on a request that carried
# response_format moves one step down; a 400 on step 3 is a real error.
MODE_JSON_SCHEMA = 1
MODE_JSON_OBJECT = 2
MODE_PLAIN = 3
MODES: tuple[int, ...] = (MODE_JSON_SCHEMA, MODE_JSON_OBJECT, MODE_PLAIN)

JUDGE_TEMPERATURE = 0
SCHEMA_NAME = 'answer_compare_adjudication'

# An answer compromises blinding when its own text names its own source. Case-
# insensitive, per provider. Naming a *different* provider is not a leak.
BLINDING_MARKERS: dict[str, tuple[str, ...]] = {
    'chatgpt': ('chatgpt', 'openai', 'gpt-'),
    'gemini': ('gemini', 'google'),
    'vesqor': ('vesqor',),
}

# The step that worked last time, keyed by (provider, model). The resolver reads
# the model from the environment at call time, so a model change must not
# inherit another model's step. Dropped on any 400 at the cached step.
_MODE_CACHE: dict[tuple[str, str], tuple[int, bool]] = {}


def reset_mode_cache() -> None:
    _MODE_CACHE.clear()


####################
# Blinding
####################


class LabeledAnswer(BaseModel):
    label: str
    provider: str
    revision: int
    text: str


def default_rng() -> random.Random:
    """The application's shuffle source: the OS CSPRNG. Tests replace this
    function with a seeded ``random.Random`` — that is the injection point."""
    return random.SystemRandom()


def shuffle_labels(
    answers: list[tuple[str, int, str]],
    rng: Optional[random.Random] = None,
) -> list[LabeledAnswer]:
    """Assign A/B/C to (provider, revision, text) triples in a fresh random order.

    ``rng`` is injectable so tests can seed it; the application passes nothing
    and gets ``default_rng()``. Two calls for the same run can and should
    produce different maps.
    """
    if not answers:
        return []
    if len(answers) > len(LABELS):
        raise ValueError(f'at most {len(LABELS)} answers can be judged, got {len(answers)}')

    order = list(answers)
    (rng or default_rng()).shuffle(order)
    return [
        LabeledAnswer(label=label, provider=provider, revision=revision, text=text)
        for label, (provider, revision, text) in zip(LABELS, order)
    ]


def label_map_of(labeled: list[LabeledAnswer]) -> dict[str, str]:
    """label -> provider, the shape stored with the report."""
    return {item.label: item.provider for item in labeled}


def judged_versions_of(labeled: list[LabeledAnswer]) -> list[dict[str, Any]]:
    """Exactly which answer versions were judged, in the fixed provider order."""
    by_provider = {item.provider: item.revision for item in labeled}
    return [{'provider': p, 'revision': by_provider[p]} for p in GENERATOR_IDS if p in by_provider]


def missing_providers_of(judged_versions: list[dict[str, Any]]) -> list[str]:
    """Providers with no complete answer at judging time — derived, never stored."""
    present = {item['provider'] for item in judged_versions}
    return [p for p in GENERATOR_IDS if p not in present]


def detect_blinding_leaks(labeled: list[LabeledAnswer]) -> list[str]:
    """Providers whose own answer text names their own source.

    The text is never altered and the judge is not told; the report is annotated.
    """
    leaks: list[str] = []
    for item in labeled:
        lowered = item.text.lower()
        markers = BLINDING_MARKERS.get(item.provider, ())
        if any(marker in lowered for marker in markers):
            leaks.append(item.provider)
    return [p for p in GENERATOR_IDS if p in leaks]


####################
# Adjudication request assembly
####################


def build_request(
    prompt: str,
    reference: Optional[str],
    labeled: list[LabeledAnswer],
    mode: str = adjudication.PROJECTION_FULL,
) -> AdjudicationRequest:
    """Assemble the canonical request from one run's blinded answers.

    This is where the evidence hierarchy is fixed: ``reference`` becomes the
    authoritative corpus, the run prompt becomes the task requirements, and the
    answers become candidates. Nothing an answer says can move it across that
    line, which is the whole point of building the request here rather than
    concatenating documents and letting the model sort them out.
    """
    return AdjudicationRequest(
        task_requirements=prompt,
        evidence=adjudication.split_reference(reference),
        candidates={item.label: item.text for item in labeled},
        mode=mode,
    )


def build_messages(
    prompt: str,
    reference: Optional[str],
    labeled: list[LabeledAnswer],
) -> list[dict[str, str]]:
    """Two messages, nothing else. The scaffolding never names a provider."""
    return adjudication.build_messages(build_request(prompt, reference, labeled))


def judge_input_chars(messages: list[dict[str, str]]) -> int:
    """What the judge actually reads: task, evidence, every answer, scaffolding."""
    return adjudication.input_chars(messages)


def report_schema(labels: list[str]) -> dict[str, Any]:
    return adjudication.report_schema(labels)


def schema_skeleton(labels: list[str]) -> str:
    return adjudication.schema_skeleton(labels)


def build_system_message(labels: list[str]) -> str:
    return adjudication.build_system_message(labels)


####################
# Validation and normalisation
####################


class MalformedReport(Exception):
    """The judge's output is not a valid adjudication. Retryable; never repaired.

    Kept as this module's error type so the router and its tests keep one name
    for "the judge did not produce something usable"; every actual rule that
    can raise it lives in the adjudication engine.
    """

    code = 'malformed_report'

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_report(content: str, labels: list[str], request: AdjudicationRequest, budget=None) -> dict[str, Any]:
    """Validate the judge's output and normalise it into the stored report.

    The stored shape carries three things: the engine version it was produced
    under, the model's findings exactly as returned (the audit trail of what the
    judge actually said), and the normalised result whose every number the
    server computed. The legacy ``verdict`` block is derived from the normalised
    result so the tally keeps one vocabulary.
    """
    budget = budget if budget is not None else adjudication.BudgetOutcome(fits=True)
    try:
        raw = adjudication.parse_adjudication(content, labels, [item.id for item in request.evidence])
        normalized = adjudication.normalize(raw, request, budget)
    except MalformedAdjudication as err:
        raise MalformedReport(err.reason) from None

    return {
        'engine_version': ADJUDICATION_ENGINE_VERSION,
        'rubric_version': ADJUDICATION_RUBRIC_VERSION,
        'raw': raw.model_dump(),
        'normalized': normalized.model_dump(),
        'verdict': _verdict_block(normalized),
        'answers': _finding_buckets(normalized),
        'rationale': normalized.final_adjudication,
        'needs_verification': normalized.needs_verification,
    }


def _verdict_block(normalized: NormalizedAdjudication) -> dict[str, Any]:
    """The verdict in the tally's vocabulary, derived — never independently decided."""
    if normalized.verdict_kind == VERDICT_WINNER and normalized.overall_winner:
        return {'kind': VERDICT_WINNER, 'labels': [normalized.overall_winner]}
    if normalized.verdict_kind == VERDICT_TIE:
        return {'kind': VERDICT_TIE, 'labels': list(normalized.tied_labels)}
    return {'kind': VERDICT_NO_RELIABLE_WINNER, 'labels': []}


def _as_findings(items: list) -> list[dict[str, str]]:
    return [{'passage': item.get('passage', ''), 'note': item.get('note', '')} for item in items]


def _penalty_findings(normalized: NormalizedAdjudication, label: str) -> list[dict[str, str]]:
    """Priced defects rendered as findings, so the summary can print them.

    The note carries the classification and the deduction, because a reader of
    the narrative must be able to tell a factual error from an unsupported
    claim without opening the structured result.
    """
    for score in normalized.candidate_scores:
        if score.label != label:
            continue
        return [
            {
                'passage': penalty.passage,
                'note': f'[{penalty.kind} / {penalty.severity}, −{penalty.points:.1f}] {penalty.note}'.strip(),
            }
            for penalty in score.penalties
        ]
    return []


def _finding_buckets(normalized: NormalizedAdjudication) -> list[dict[str, Any]]:
    """The canonical result projected into the narrative's six buckets.

    Everything here is a view of findings already made: no bucket is a second
    judgement, and a defect that was priced appears with its price.
    """
    dumped = normalized.model_dump()
    buckets: list[dict[str, Any]] = []
    for label in normalized.labels:
        errors = _as_findings(dumped['critical_errors'].get(label, []))
        errors.extend(_penalty_findings(normalized, label))
        buckets.append(
            {
                'label': label,
                'strengths': _as_findings(dumped['best_in_class_findings'].get(label, [])),
                'errors_or_unsupported': errors,
                'omissions': _as_findings(dumped['critical_omissions'].get(label, [])),
                'useful_extras': _as_findings(dumped['useful_extras'].get(label, [])),
                'unnecessary': _as_findings(dumped['unnecessary'].get(label, [])),
                'improvements': _as_findings(dumped['improvements'].get(label, [])),
            }
        )
    return buckets


class UnmappedLabel(Exception):
    """A label in the stored report is not in the stored label map.

    After validation this is practically unreachable; if it happens it is a
    server-side invariant violation found at read time on an already-settled
    row, and the caller answers rather than mutates.
    """

    code = 'label_not_in_map'

    def __init__(self, label: Any) -> None:
        super().__init__(f'label {label!r} is not in the label map')
        self.label = label


def map_report(report: dict[str, Any], label_map: dict[str, str]) -> dict[str, Any]:
    """Apply the stored label map on read.

    The stored report keeps its anonymous labels — that is the audit trail of
    what the judge actually saw and said. This is the one implementation of the
    transform: the page renders it, the tally counts from it and the summary
    quotes it, so they cannot disagree about who won.
    """

    def provider_of(label: Any) -> str:
        if not isinstance(label, str) or label not in label_map:
            raise UnmappedLabel(label)
        return label_map[label]

    def remap(mapping: Optional[dict]) -> dict[str, Any]:
        return {provider_of(label): value for label, value in (mapping or {}).items()}

    answers = {provider_of(item['label']): item for item in report.get('answers', [])}
    verdict = report.get('verdict', {})
    normalized = report.get('normalized') or {}

    mapped: dict[str, Any] = {
        'answers': answers,
        'verdict': {
            'kind': verdict.get('kind'),
            'providers': [provider_of(label) for label in verdict.get('labels', [])],
        },
        'rationale': report.get('rationale'),
        'needs_verification': report.get('needs_verification', []),
    }

    if normalized:
        winner = normalized.get('overall_winner')
        mapped['adjudication'] = {
            'engine_version': normalized.get('engine_version'),
            'rubric_version': normalized.get('rubric_version'),
            'scores': remap(normalized.get('scores')),
            'category_scores': remap(normalized.get('category_scores')),
            'category_weights': normalized.get('category_weights', {}),
            'category_winners': {
                key: [provider_of(label) for label in labels]
                for key, labels in (normalized.get('category_winners') or {}).items()
            },
            'overall_winner': provider_of(winner) if winner else None,
            'overall_ranking': [provider_of(label) for label in normalized.get('overall_ranking', [])],
            'tied_providers': [provider_of(label) for label in normalized.get('tied_labels', [])],
            'winning_margin': normalized.get('winning_margin'),
            'tie_break_used': normalized.get('tie_break_used'),
            'confidence': normalized.get('confidence'),
            'confidence_reasons': normalized.get('confidence_reasons', []),
            'decisive_reasons': normalized.get('decisive_reasons', []),
            'claim_validation_summary': remap(normalized.get('claim_validation_summary')),
            'pairwise_results': [
                {
                    **item,
                    'first': provider_of(item['first']),
                    'second': provider_of(item['second']),
                    'stronger': provider_of(item['stronger']) if item.get('stronger') != 'tie' else 'tie',
                }
                for item in normalized.get('pairwise_results', [])
            ],
            'candidate_scores': [
                {**score, 'provider': provider_of(score['label'])} for score in normalized.get('candidate_scores', [])
            ],
            'winner_gap_analysis': normalized.get('winner_gap_analysis', []),
            'loser_recovery_analysis': remap(normalized.get('loser_recovery_analysis')),
            'unresolved_uncertainty': normalized.get('unresolved_uncertainty', []),
            'evidence_complete': normalized.get('evidence_complete', True),
            'dropped_evidence_ids': normalized.get('dropped_evidence_ids', []),
            'truncated_providers': [provider_of(label) for label in normalized.get('truncated_candidate_labels', [])],
            'injection_signals': [
                {**signal, 'provider': provider_of(signal['label'])}
                for signal in normalized.get('injection_signals', [])
            ],
            'final_adjudication': normalized.get('final_adjudication', ''),
        }

    return mapped


####################
# Structured-output ladder
####################


class JudgeCallResult(BaseModel):
    """What one judge call produced, before validation."""

    content: str
    model: Optional[str] = None
    engine_version: Optional[str] = None
    # The body fields actually sent on the attempt that succeeded, plus the
    # ladder diagnostics: the step used and every rejection along the way.
    params: dict


CompletionFn = Callable[..., Awaitable[client.CompletionResult]]


def _response_format(mode: int, labels: list[str]) -> Optional[dict[str, Any]]:
    if mode == MODE_JSON_SCHEMA:
        return {
            'type': 'json_schema',
            'json_schema': {'name': SCHEMA_NAME, 'strict': True, 'schema': report_schema(labels)},
        }
    if mode == MODE_JSON_OBJECT:
        return {'type': 'json_object'}
    return None


def _extra_body(mode: int, labels: list[str], with_temperature: bool) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if with_temperature:
        extra['temperature'] = JUDGE_TEMPERATURE
    response_format = _response_format(mode, labels)
    if response_format is not None:
        extra['response_format'] = response_format
    return extra


# The statuses a door uses to refuse a request *shape* before generating: 400 from
# most gateways, 422 from a FastAPI door validating the body with extra='forbid' —
# the likeliest answer from the VESQOR door to an unknown ``response_format``.
REJECTION_STATUSES: tuple[int, ...] = (400, 422)


def _is_rejection(err: client.ProviderCallError) -> bool:
    """A 400-class refusal is a rejection before generation: it costs nothing and
    means "try the next shape". Not a keyword match — providers word these
    differently. The one such refusal that is not about the request shape is a
    context-length one, which the client already types separately; walking the
    ladder on it would only record false rejections and end with the same error."""
    return err.status in REJECTION_STATUSES and not isinstance(err, client.ContextLengthExceededError)


async def call_judge(
    provider_id: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    labels: list[str],
    completion: CompletionFn = client.chat_completion,
) -> JudgeCallResult:
    """Call the judge, walking the structured-output ladder as needed.

    Composition of the two retries is fixed: on a 400, drop ``temperature``
    first (same mode); only if the request without it is also rejected move down
    the mode ladder. Reasoning models reject ``temperature`` while supporting
    ``json_schema`` — the other order would record mode 3 for a provider where
    mode 1 works, and later diagnostics would lie.

    ``completion`` is injectable for tests; the application uses the client.
    """
    cache_key = (provider_id, model)
    cached = _MODE_CACHE.get(cache_key)
    mode, with_temperature = cached if cached is not None else (MODE_JSON_SCHEMA, True)
    from_cache = cached is not None

    rejections: list[dict[str, Any]] = []

    while True:
        extra = _extra_body(mode, labels, with_temperature)
        try:
            result = await completion(base_url, api_key, model, messages, extra_body=extra)
        except client.ProviderCallError as err:
            if not _is_rejection(err):
                raise

            rejection = {
                'mode': mode,
                'temperature': with_temperature,
                'status': err.status,
                'message': err.upstream_message or '',
            }
            rejections.append(rejection)
            log.warning(
                'judge %s (%s) rejected mode=%s temperature=%s: %s',
                provider_id,
                model,
                mode,
                with_temperature,
                rejection['message'],
            )

            if from_cache:
                # The remembered step no longer works: forget it and start over.
                _MODE_CACHE.pop(cache_key, None)
                from_cache = False
                mode, with_temperature = MODE_JSON_SCHEMA, True
                continue

            if with_temperature:
                with_temperature = False
                continue

            if mode < MODE_PLAIN:
                mode += 1
                with_temperature = True
                continue

            # Step 3 carried no response_format: this 400 is a real error.
            raise

        _MODE_CACHE[cache_key] = (mode, with_temperature)
        return JudgeCallResult(
            content=result.content,
            model=result.model,
            engine_version=result.engine_version,
            params={
                **result.params,
                'structured_output_mode': mode,
                'structured_output_rejections': rejections,
            },
        )
