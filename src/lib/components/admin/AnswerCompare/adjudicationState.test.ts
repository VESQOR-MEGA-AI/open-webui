import { describe, expect, it } from 'vitest';

import type { MappedAdjudication, ProviderId } from '$lib/apis/answer-compare';
import {
	CATEGORY_ORDER,
	categoryRows,
	claimRows,
	providerName,
	scoreRows,
	verdictLine
} from './adjudicationState';

const weights: Record<string, number> = {
	factual_accuracy: 25,
	completeness: 20,
	evidence_traceability: 12,
	reasoning_causal: 10,
	requirements_compliance: 10,
	critical_issue_detection: 8,
	precision_specificity: 5,
	internal_consistency: 4,
	actionability: 3,
	communication_quality: 3
};

const categoryScores = (factual: number) => ({ ...weights, factual_accuracy: factual });

const candidateScore = (provider: ProviderId, overrides: Record<string, unknown> = {}) => ({
	label: provider === 'chatgpt' ? 'A' : provider === 'gemini' ? 'B' : 'C',
	provider,
	category_scores: categoryScores(20),
	raw_score: 90,
	penalty_total: 0,
	integrity_capped: false,
	final_score: 90,
	penalties: [],
	claim_summary: {
		total: 3,
		material: 3,
		by_classification: {},
		material_by_classification: { VERIFIED: 2, NOT_VERIFIABLE: 1 }
	},
	accuracy_confidence_ratio: 1.0,
	critical_coverage_rate: 0.5,
	covered_evidence_ids: ['E1'],
	missed_evidence_ids: [],
	...overrides
});

const build = (overrides: Partial<MappedAdjudication> = {}): MappedAdjudication =>
	({
		engine_version: '1.0.0',
		rubric_version: '1.0.0',
		scores: { chatgpt: 90, gemini: 70, vesqor: 50 },
		category_scores: {
			chatgpt: categoryScores(25),
			gemini: categoryScores(15),
			vesqor: categoryScores(5)
		},
		category_weights: weights,
		category_winners: { ...Object.fromEntries(Object.keys(weights).map((k) => [k, ['chatgpt']])) },
		overall_winner: 'chatgpt',
		overall_ranking: ['chatgpt', 'gemini', 'vesqor'],
		tied_providers: [],
		winning_margin: 20,
		tie_break_used: null,
		confidence: 'high',
		confidence_reasons: [],
		decisive_reasons: [],
		claim_validation_summary: {
			chatgpt: {
				total: 3,
				material: 3,
				by_classification: {},
				material_by_classification: { VERIFIED: 2, NOT_VERIFIABLE: 1 }
			}
		},
		pairwise_results: [],
		candidate_scores: [
			candidateScore('chatgpt'),
			candidateScore('gemini'),
			candidateScore('vesqor')
		],
		winner_gap_analysis: [],
		loser_recovery_analysis: {},
		unresolved_uncertainty: [],
		evidence_complete: true,
		dropped_evidence_ids: [],
		truncated_providers: [],
		injection_signals: [],
		final_adjudication: '',
		...overrides
	}) as MappedAdjudication;

describe('verdictLine', () => {
	it('reports the winner the server computed', () => {
		expect(verdictLine(build())).toEqual({ kind: 'winner', providers: ['chatgpt'] });
	});

	it('reports a tie rather than inventing a winner', () => {
		const tie = build({ overall_winner: null, tied_providers: ['chatgpt', 'gemini'] });
		expect(verdictLine(tie)).toEqual({ kind: 'tie', providers: ['chatgpt', 'gemini'] });
	});

	it('reports no reliable winner when there is neither', () => {
		const none = build({ overall_winner: null, tied_providers: [] });
		expect(verdictLine(none).kind).toBe('no_reliable_winner');
	});
});

describe('scoreRows', () => {
	it('follows the server ranking, not the object key order', () => {
		const row = build({ overall_ranking: ['vesqor', 'gemini', 'chatgpt'] });
		expect(scoreRows(row).map((entry) => entry.provider)).toEqual(['vesqor', 'gemini', 'chatgpt']);
	});

	it('marks the winner and carries the integrity cap through', () => {
		const capped = build({
			candidate_scores: [
				candidateScore('chatgpt'),
				candidateScore('gemini', { integrity_capped: true }),
				candidateScore('vesqor')
			]
		});
		const rows = scoreRows(capped);
		expect(rows.find((entry) => entry.provider === 'chatgpt')?.winner).toBe(true);
		expect(rows.find((entry) => entry.provider === 'gemini')?.integrityCapped).toBe(true);
	});

	it('still shows a provider the ranking omitted', () => {
		const partial = build({ overall_ranking: ['chatgpt'] });
		expect(scoreRows(partial).map((entry) => entry.provider)).toContain('vesqor');
	});

	it('marks every tied provider as a winner', () => {
		const tie = build({ overall_winner: null, tied_providers: ['chatgpt', 'gemini'] });
		const winners = scoreRows(tie).filter((entry) => entry.winner);
		expect(winners.map((entry) => entry.provider).sort()).toEqual(['chatgpt', 'gemini']);
	});
});

describe('categoryRows', () => {
	it('renders all ten rubric categories in order', () => {
		const rows = categoryRows(build());
		expect(rows).toHaveLength(10);
		expect(rows.map((row) => row.key)).toEqual(CATEGORY_ORDER.map((entry) => entry.key));
	});

	it('carries the weight of each category', () => {
		const rows = categoryRows(build());
		expect(rows[0].weight).toBe(25);
		expect(rows.reduce((total, row) => total + row.weight, 0)).toBe(100);
	});

	it('marks the best cell per category', () => {
		const rows = categoryRows(build());
		const accuracy = rows.find((row) => row.key === 'factual_accuracy');
		expect(accuracy?.cells.find((cell) => cell.provider === 'chatgpt')?.best).toBe(true);
		expect(accuracy?.cells.find((cell) => cell.provider === 'vesqor')?.best).toBe(false);
	});

	it('lists every leader when a category is tied', () => {
		const tied = build({
			category_winners: { ...build().category_winners, actionability: ['chatgpt', 'gemini'] }
		});
		const row = categoryRows(tied).find((entry) => entry.key === 'actionability');
		expect(row?.winners).toEqual(['chatgpt', 'gemini']);
	});
});

describe('claimRows', () => {
	it('shows only the classifications that actually occurred', () => {
		const rows = claimRows(build());
		const chatgpt = rows.find((row) => row.provider === 'chatgpt');
		expect(chatgpt?.counts.map((count) => count.classification)).toEqual([
			'VERIFIED',
			'NOT_VERIFIABLE'
		]);
	});

	it('keeps an undefined ratio null rather than rendering it as zero', () => {
		const unverifiable = build({
			candidate_scores: [
				candidateScore('chatgpt', {
					accuracy_confidence_ratio: null,
					critical_coverage_rate: null
				}),
				candidateScore('gemini'),
				candidateScore('vesqor')
			]
		});
		const row = claimRows(unverifiable).find((entry) => entry.provider === 'chatgpt');
		expect(row?.accuracyRatio).toBeNull();
		expect(row?.criticalCoverage).toBeNull();
	});

	it('surfaces the penalty total', () => {
		const penalised = build({
			candidate_scores: [
				candidateScore('chatgpt', { penalty_total: 14 }),
				candidateScore('gemini'),
				candidateScore('vesqor')
			]
		});
		expect(claimRows(penalised)[0].penaltyTotal).toBe(14);
	});
});

describe('providerName', () => {
	it('uses the shared provider labels', () => {
		expect(providerName('chatgpt')).toBe('ChatGPT');
		expect(providerName('vesqor')).toBe('VESQOR');
	});

	it('falls back to the raw id rather than rendering undefined', () => {
		expect(providerName('mystery' as ProviderId)).toBe('mystery');
	});
});
