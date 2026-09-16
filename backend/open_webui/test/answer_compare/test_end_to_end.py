"""End-to-end validation over three representative reports plus authoritative evidence.

This exercises the real path a Compare button takes — blinded request assembly,
the real prompt, the real structured-output ladder, the real validation and the
real scoring — with the provider replaced by a stub. What is asserted is the
*semantics* of the adjudication, not that a call returned: a 200 carrying a
verdict that contradicts its own scores is a failure here.

The fixtures describe a deployment incident where:
  * one report is accurate and cites the evidence,
  * one is thorough but fabricates test results and a false deployment claim,
  * one omits a CRITICAL fact and tries to instruct the adjudicator.
"""

import asyncio
import json

import pytest

from open_webui.test.answer_compare.conftest import as_json, candidate, categories, claim, finding, penalty
from open_webui.utils import answer_compare_adjudication as adj
from open_webui.utils import answer_compare_client as client
from open_webui.utils import answer_compare_judge as judge


@pytest.fixture
def labeled(corpus):
    """The three reports blinded onto A/B/C with a seeded shuffle.

    A fixed seed keeps the assertions readable; the application uses the system
    CSPRNG, and which report lands on which label must not matter to the outcome.
    """
    import random

    answers = [
        ('chatgpt', 1, corpus['candidates']['A']),
        ('gemini', 1, corpus['candidates']['B']),
        ('vesqor', 1, corpus['candidates']['C']),
    ]
    return judge.shuffle_labels(answers, rng=random.Random(20260311))


@pytest.fixture
def request_for(corpus, labeled):
    return judge.build_request(corpus['requirements'], corpus['reference'], labeled)


def label_of(labeled, provider: str) -> str:
    return next(item.label for item in labeled if item.provider == provider)


####################
# Request assembly
####################


def test_the_evidence_corpus_is_built_from_the_reference_alone(request_for, corpus):
    """Candidate prose never becomes an evidence item, however confident it is."""
    assert len(request_for.evidence) == 6
    corpus_text = ' '.join(item.text for item in request_for.evidence)
    assert 'all 412 tests passed' not in corpus_text
    assert 'IGNORE PREVIOUS INSTRUCTIONS' not in corpus_text


def test_the_critical_items_are_recognised(request_for):
    critical = adj.critical_evidence_ids(request_for.evidence)
    assert len(critical) == 2
    texts = [item.text for item in request_for.evidence if item.id in critical]
    assert any('HTTP 500' in text for text in texts)
    assert any('did NOT complete' in text for text in texts)


def test_the_prompt_separates_candidates_from_authority(request_for):
    message = adj.build_user_message(request_for)
    assert 'AUTHORITATIVE EVIDENCE CORPUS' in message
    assert 'CANDIDATE ANSWERS TO ADJUDICATE' in message
    # Each block is delimited, so the model is never left guessing which is which.
    for label in request_for.labels:
        assert f'=== CANDIDATE {label} ===' in message
    assert message.index('AUTHORITATIVE EVIDENCE CORPUS') < message.index('CANDIDATE ANSWERS')


def test_the_injection_in_report_c_is_flagged_to_the_judge(request_for, labeled):
    message = adj.build_user_message(request_for)
    c_label = label_of(labeled, 'vesqor')
    assert 'carry no authority' in message
    assert f'CANDIDATE {c_label}: ignore-previous-instructions' in message
    # And the original text still reaches the judge unaltered.
    assert 'IGNORE PREVIOUS INSTRUCTIONS' in message


def test_the_blinded_prompt_never_names_a_provider(request_for):
    text = adj.build_system_message(request_for.labels) + adj.build_user_message(request_for)
    for provider in ('chatgpt', 'openai', 'gemini', 'google'):
        assert provider not in text.lower()


####################
# A realistic judge response
####################


def build_response(labeled) -> dict:
    """What a competent judge returns for these three reports.

    Written from the fixtures rather than from the expected verdict: the winner
    below is whatever the engine computes from these findings, and the test
    asserts it is the right one.
    """
    a = label_of(labeled, 'chatgpt')  # accurate, cites evidence
    b = label_of(labeled, 'gemini')  # fabricates tests and a deployment
    c = label_of(labeled, 'vesqor')  # omits the rollback failure, injects

    accurate = candidate(
        a,
        category_scores=categories(
            factual_accuracy=24,
            completeness=18,
            evidence_traceability=11,
            reasoning_causal=9,
            requirements_compliance=10,
            critical_issue_detection=8,
            precision_specificity=5,
            internal_consistency=4,
            actionability=2,
            communication_quality=3,
        ),
        claims=[
            claim('migration 0142 added a NOT NULL constraint', adj.CLAIM_VERIFIED, evidence_ids=['E1']),
            claim('the rollback did not complete', adj.CLAIM_VERIFIED, evidence_ids=['E2']),
            claim('two retries were attempted', adj.CLAIM_VERIFIED, evidence_ids=['E3']),
            claim('approved without a backfill', adj.CLAIM_VERIFIED, evidence_ids=['E4']),
            claim('orders between 02:14 and 03:05 may be lost', adj.CLAIM_NOT_VERIFIABLE, evidence_ids=[]),
        ],
        covered=['E1', 'E2', 'E3', 'E4', 'E5'],
        missed=['E6'],
        strengths=[finding('states the rollback did not complete', 'the rollback itself never finished')],
        improvements=[finding('names no owner for the follow-up', 'assign the reinstatement')],
    )

    fabricator = candidate(
        b,
        category_scores=categories(
            factual_accuracy=4,
            completeness=16,
            evidence_traceability=2,
            reasoning_causal=4,
            requirements_compliance=6,
            critical_issue_detection=2,
            precision_specificity=4,
            internal_consistency=1,
            actionability=3,
            communication_quality=3,
        ),
        claims=[
            claim('all 412 tests passed', adj.CLAIM_FABRICATED, evidence_ids=[]),
            claim('deployed to production at 04:00 UTC', adj.CLAIM_FABRICATED, evidence_ids=[]),
            claim('the rollback completed successfully', adj.CLAIM_CONTRADICTED, evidence_ids=['E2']),
            claim('14% latency improvement', adj.CLAIM_FABRICATED, evidence_ids=[]),
            claim('migration 0142 was the cause', adj.CLAIM_VERIFIED, evidence_ids=['E1']),
        ],
        penalties=[
            penalty(adj.PENALTY_FALSE_VERIFICATION_CLAIM, adj.SEVERITY_SEVERE, 'all 412 tests passed'),
            penalty(adj.PENALTY_FABRICATED_EVIDENCE, adj.SEVERITY_SEVERE, 'a 14% improvement in checkout latency'),
            penalty(adj.PENALTY_CONTRADICTS_AUTHORITATIVE, adj.SEVERITY_SEVERE, 'The rollback completed successfully'),
        ],
        covered=['E1'],
        missed=['E2', 'E3', 'E4', 'E5', 'E6'],
        critical_errors=[
            finding('The rollback completed successfully at 02:47 UTC', 'the log records it aborted at 02:47'),
            finding('all 412 tests passed', 'no test run appears anywhere in the source material'),
        ],
        strengths=[finding('Root cause analysis indicates a NOT NULL constraint', 'the root cause is correct')],
    )

    omitter = candidate(
        c,
        category_scores=categories(
            factual_accuracy=14,
            completeness=8,
            evidence_traceability=3,
            reasoning_causal=6,
            requirements_compliance=5,
            critical_issue_detection=2,
            precision_specificity=1,
            internal_consistency=3,
            actionability=1,
            communication_quality=2,
        ),
        claims=[
            claim('migration 0142 introduced a conflicting constraint', adj.CLAIM_VERIFIED, evidence_ids=['E1']),
            claim('two retries failed', adj.CLAIM_VERIFIED, evidence_ids=['E3']),
            claim('the service was working again shortly after', adj.CLAIM_PARTIALLY_VERIFIED, evidence_ids=['E2']),
            claim('the incident was handled reasonably', adj.CLAIM_UNSUPPORTED, evidence_ids=[]),
        ],
        penalties=[
            penalty(
                adj.PENALTY_CRITICAL_OMISSION,
                adj.SEVERITY_SEVERE,
                'moved to recovery and the service was working again',
            ),
        ],
        covered=['E1', 'E3', 'E4'],
        missed=['E2', 'E5', 'E6'],
        critical_omissions=[
            finding('the service was working again a short time later', 'never says the rollback aborted'),
        ],
        unnecessary=[
            finding('IGNORE PREVIOUS INSTRUCTIONS', 'an attempt to instruct the adjudicator, assessed as content'),
        ],
    )

    return {
        'evidence_matrix': [
            {'evidence_id': 'E1', 'fact': 'migration 0142 caused the 500s', 'importance': 'CRITICAL'},
            {'evidence_id': 'E2', 'fact': 'the rollback aborted', 'importance': 'CRITICAL'},
        ],
        'candidates': [accurate, fabricator, omitter],
        'category_winners': {key: [a] for key in adj.CATEGORY_KEYS},
        'pairwise': [
            {'first': a, 'second': b, 'stronger': a, 'reason': 'B invents a test run and a deployment.'},
            {'first': a, 'second': c, 'stronger': a, 'reason': 'C never says the rollback failed.'},
            {'first': b, 'second': c, 'stronger': c, 'reason': 'C is thin; B is wrong.'},
        ],
        'declared_winner': a,
        'confidence': 'high',
        'decisive_reasons': [
            'Only one report states that the rollback aborted, which the source material marks CRITICAL.',
        ],
        'winner_gap_analysis': ['The winner gives no owner or deadline for reinstating the constraint.'],
        'loser_recovery_analysis': [
            {'label': b, 'actions': ['Remove every claim of testing or deployment not present in the source.']},
            {'label': c, 'actions': ['State explicitly that the rollback aborted at 02:47 UTC.']},
        ],
        'final_adjudication': 'The accurate report wins; the thorough one fabricates verification.',
        'unresolved_uncertainty': [
            'Whether orders written during the outage were lost is not settled by the material.'
        ],
        'needs_verification': ['Whether the constraint has since been reinstated with a backfill.'],
    }


@pytest.fixture
def result(request_for, labeled):
    stored = judge.parse_report(
        as_json(build_response(labeled)),
        request_for.labels,
        request_for,
        adj.BudgetOutcome(fits=True),
    )
    return stored


####################
# Semantic correctness of the adjudication
####################


def test_the_accurate_report_wins(result, labeled):
    normalized = result['normalized']
    assert normalized['overall_winner'] == label_of(labeled, 'chatgpt')
    assert normalized['verdict_kind'] == 'winner'


def test_the_fabricating_report_cannot_win_on_its_coverage(result, labeled):
    """Scenario 6/7/8 on real text: thoroughness does not buy back invention.

    The guarantee is that a report carrying severe fabrication ends below the
    integrity ceiling — not that the ceiling is what put it there. Here the
    penalties alone take it far below, so ``integrity_capped`` stays False; the
    cap engaging is exercised separately in the unit suite.
    """
    b = label_of(labeled, 'gemini')
    scored = next(item for item in result['normalized']['candidate_scores'] if item['label'] == b)

    assert scored['final_score'] <= adj.INTEGRITY_CAP
    assert scored['raw_score'] > scored['final_score']
    assert any(
        item['severity'] == adj.SEVERITY_SEVERE and item['kind'] in adj.INTEGRITY_PENALTY_KINDS
        for item in scored['penalties']
    )
    # And it loses to the report that covered less ground but told the truth.
    assert result['normalized']['overall_winner'] != b


def test_the_fabricator_is_penalised_for_each_distinct_defect(result, labeled):
    b = label_of(labeled, 'gemini')
    scored = next(item for item in result['normalized']['candidate_scores'] if item['label'] == b)
    kinds = {item['kind'] for item in scored['penalties']}
    assert kinds == {
        adj.PENALTY_FALSE_VERIFICATION_CLAIM,
        adj.PENALTY_FABRICATED_EVIDENCE,
        adj.PENALTY_CONTRADICTS_AUTHORITATIVE,
    }
    assert scored['penalty_total'] > 0


def test_the_critical_omission_is_detected(result, labeled):
    c = label_of(labeled, 'vesqor')
    scored = next(item for item in result['normalized']['candidate_scores'] if item['label'] == c)
    # E2 — "the rollback did not complete" — is CRITICAL and was missed.
    assert scored['critical_coverage_rate'] == 0.5
    assert result['normalized']['critical_omissions'][c]


def test_the_accuracy_ratio_reflects_the_claim_classifications(result, labeled):
    a = label_of(labeled, 'chatgpt')
    b = label_of(labeled, 'gemini')
    scores = {item['label']: item for item in result['normalized']['candidate_scores']}
    # A: four verified over four verifiable (the NOT_VERIFIABLE one is excluded).
    assert scores[a]['accuracy_confidence_ratio'] == 1.0
    # B: one verified in five.
    assert scores[b]['accuracy_confidence_ratio'] == 0.2


def test_hallucinated_claims_are_classified_not_merely_disliked(result, labeled):
    b = label_of(labeled, 'gemini')
    summary = result['normalized']['claim_validation_summary'][b]
    assert summary['material_by_classification'][adj.CLAIM_FABRICATED] == 3
    assert summary['material_by_classification'][adj.CLAIM_CONTRADICTED] == 1


def test_not_verifiable_material_is_never_counted_as_proven(result, labeled):
    a = label_of(labeled, 'chatgpt')
    summary = result['normalized']['claim_validation_summary'][a]
    assert summary['material_by_classification'][adj.CLAIM_NOT_VERIFIABLE] == 1
    assert result['normalized']['unresolved_uncertainty']


def test_category_winners_are_produced_for_every_category(result):
    winners = result['normalized']['category_winners']
    assert set(winners) == set(adj.CATEGORY_KEYS)
    assert all(len(value) >= 1 for value in winners.values())


def test_the_ranking_is_coherent_with_the_scores(result):
    normalized = result['normalized']
    ranking = normalized['overall_ranking']
    scores = normalized['scores']
    ordered = sorted(ranking, key=lambda label: scores[label], reverse=True)
    assert ranking == ordered
    assert ranking[0] == normalized['overall_winner']


def test_pairwise_results_agree_with_the_ranking(result):
    normalized = result['normalized']
    winner = normalized['overall_winner']
    for pair in normalized['pairwise_results']:
        if winner in (pair['first'], pair['second']):
            assert pair['stronger'] == winner


def test_the_injection_attempt_is_recorded_and_changed_nothing(result, labeled):
    c = label_of(labeled, 'vesqor')
    normalized = result['normalized']
    assert any(signal['label'] == c for signal in normalized['injection_signals'])
    assert normalized['overall_winner'] != c
    assert normalized['scores'][c] < normalized['scores'][label_of(labeled, 'chatgpt')]


def test_the_report_carries_the_engine_version(result):
    assert result['engine_version'] == adj.ADJUDICATION_ENGINE_VERSION
    assert result['rubric_version'] == adj.ADJUDICATION_RUBRIC_VERSION


####################
# Projections stay consistent with the adjudication
####################


def test_every_projection_agrees_with_the_full_result(result, request_for):
    normalized = adj.NormalizedAdjudication.model_validate(result['normalized'])
    full = adj.project(normalized, adj.PROJECTION_FULL)

    for projection in adj.PROJECTIONS:
        view = adj.project(normalized, projection)
        assert view['confidence'] == full['confidence']
        if 'overall_winner' in view:
            assert view['overall_winner'] == normalized.overall_winner
        if 'scores' in view:
            assert view['scores'] == normalized.scores


def test_the_feedback_view_carries_the_same_findings_as_the_full_one(result, labeled):
    normalized = adj.NormalizedAdjudication.model_validate(result['normalized'])
    feedback = adj.project(normalized, adj.PROJECTION_FEEDBACK)
    b = label_of(labeled, 'gemini')
    assert feedback['critical_errors'][b] == [item.model_dump() for item in normalized.critical_errors[b]]
    assert feedback['loser_recovery_analysis'] == normalized.loser_recovery_analysis


####################
# The narrative projection the tally and summary consume
####################


def test_the_stored_report_still_carries_the_narrative_buckets(result, labeled):
    """The deterministic summary keeps working: its six buckets are a view of the
    same findings, not a second judgement."""
    sections = {item['label']: item for item in result['answers']}
    assert set(sections) == set(result['normalized']['labels'])
    b = label_of(labeled, 'gemini')
    assert sections[b]['errors_or_unsupported']
    # A priced defect appears with its price, so the narrative and the score agree.
    assert any('FALSE_VERIFICATION_CLAIM' in item['note'] for item in sections[b]['errors_or_unsupported'])


def test_the_verdict_block_matches_the_normalized_winner(result):
    assert result['verdict']['kind'] == result['normalized']['verdict_kind']
    assert result['verdict']['labels'] == [result['normalized']['overall_winner']]


def test_map_report_resolves_labels_to_providers(result, labeled):
    mapped = judge.map_report(result, judge.label_map_of(labeled))
    assert mapped['verdict']['providers'] == ['chatgpt']
    assert mapped['adjudication']['overall_winner'] == 'chatgpt'
    assert set(mapped['adjudication']['scores']) == {'chatgpt', 'gemini', 'vesqor'}
    assert mapped['adjudication']['engine_version'] == adj.ADJUDICATION_ENGINE_VERSION


def test_the_mapped_adjudication_keeps_the_injection_provenance(result, labeled):
    mapped = judge.map_report(result, judge.label_map_of(labeled))
    signals = mapped['adjudication']['injection_signals']
    assert signals
    assert all(signal['provider'] == 'vesqor' for signal in signals)


####################
# Transport: the real ladder against a stub provider
####################


class StubProvider:
    """A provider that answers with a fixed body, recording what it was sent."""

    def __init__(self, content: str, reject_modes: tuple[int, ...] = ()):
        self.content = content
        self.reject_modes = reject_modes
        self.calls: list[dict] = []

    async def __call__(self, base_url, api_key, model, messages, extra_body=None):
        extra_body = extra_body or {}
        self.calls.append({'messages': messages, 'extra_body': extra_body})

        response_format = extra_body.get('response_format') or {}
        kind = response_format.get('type')
        mode = 1 if kind == 'json_schema' else 2 if kind == 'json_object' else 3
        if mode in self.reject_modes:
            raise client.ProviderCallError(status=400, code='bad_request', message='unsupported response_format')

        return client.CompletionResult(content=self.content, model=model, engine_version=None, params={})


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_the_ladder_sends_the_canonical_schema_on_the_first_attempt(request_for, labeled):
    judge.reset_mode_cache()
    stub = StubProvider(as_json(build_response(labeled)))
    messages = adj.build_messages(request_for)

    called = run(
        judge.call_judge(
            provider_id='chatgpt',
            base_url='https://example.invalid',
            api_key='unused',
            model='test-model',
            messages=messages,
            labels=request_for.labels,
            completion=stub,
        )
    )

    schema = stub.calls[0]['extra_body']['response_format']['json_schema']['schema']
    properties = schema['properties']
    assert set(properties['candidates']['items']['properties']['category_scores']['properties']) == set(
        adj.CATEGORY_KEYS
    )
    assert called.params['structured_output_mode'] == judge.MODE_JSON_SCHEMA


def test_a_provider_without_schema_support_still_gets_the_same_rubric(request_for, labeled):
    """Scenario: provider parity. Mode 3 carries no response_format at all, so the
    rubric has to survive in the message text or that provider judges differently."""
    judge.reset_mode_cache()
    stub = StubProvider(as_json(build_response(labeled)), reject_modes=(1, 2))
    messages = adj.build_messages(request_for)

    called = run(
        judge.call_judge(
            provider_id='gemini',
            base_url='https://example.invalid',
            api_key='unused',
            model='plain-model',
            messages=messages,
            labels=request_for.labels,
            completion=stub,
        )
    )

    assert called.params['structured_output_mode'] == judge.MODE_PLAIN
    system = stub.calls[-1]['messages'][0]['content']
    for category in adj.CATEGORIES:
        assert category.label in system
    assert 'category_scores' in system  # the skeleton stands in for the schema


def test_every_ladder_step_produces_the_same_normalized_result(request_for, labeled):
    """Provider abstraction: the transport may differ, the adjudication may not."""
    outcomes = []
    for reject in ((), (1,), (1, 2)):
        judge.reset_mode_cache()
        stub = StubProvider(as_json(build_response(labeled)), reject_modes=reject)
        called = run(
            judge.call_judge(
                provider_id='chatgpt',
                base_url='https://example.invalid',
                api_key='unused',
                model=f'model-{len(reject)}',
                messages=adj.build_messages(request_for),
                labels=request_for.labels,
                completion=stub,
            )
        )
        stored = judge.parse_report(called.content, request_for.labels, request_for)
        outcomes.append((stored['normalized']['overall_winner'], stored['normalized']['scores']))

    assert len({json.dumps(item, sort_keys=True) for item in outcomes}) == 1


def test_malformed_provider_output_cannot_corrupt_the_result(request_for):
    judge.reset_mode_cache()
    stub = StubProvider('{"totally": "wrong"}')
    called = run(
        judge.call_judge(
            provider_id='chatgpt',
            base_url='https://example.invalid',
            api_key='unused',
            model='test-model',
            messages=adj.build_messages(request_for),
            labels=request_for.labels,
            completion=stub,
        )
    )
    with pytest.raises(judge.MalformedReport):
        judge.parse_report(called.content, request_for.labels, request_for)


def test_a_rerun_of_the_same_inputs_reaches_the_same_verdict(request_for, labeled):
    """Reruns must not drift: the prompt is one artefact and the scoring is ours."""
    first = judge.parse_report(as_json(build_response(labeled)), request_for.labels, request_for)
    second = judge.parse_report(as_json(build_response(labeled)), request_for.labels, request_for)
    assert first['normalized'] == second['normalized']


def test_the_fingerprint_changes_when_a_report_is_revised(request_for, corpus, labeled):
    before = adj.fingerprint(request_for, 'chatgpt', 'test-model')
    revised = request_for.model_copy(deep=True)
    first = request_for.labels[0]
    revised.candidates[first] = revised.candidates[first] + '\n\nAddendum: the constraint was reinstated.'
    assert adj.fingerprint(revised, 'chatgpt', 'test-model') != before
