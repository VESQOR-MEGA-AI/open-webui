/**
 * Pure view state for the scored adjudication panel.
 *
 * Nothing here decides anything: the winner, the scores, the category leaders
 * and the confidence all arrive computed from the server, and these functions
 * only put them in display order. Keeping it that way is the point — a second
 * place that could decide who won is a second place that could disagree.
 */

import type {
	ClaimClassification,
	MappedAdjudication,
	GeneratorId,
	VerdictKind
} from '$lib/apis/answer-compare';
import { GENERATOR_IDS, PROVIDER_LABELS } from './state';

/** Category keys in rubric order, with their display titles. Mirrors the engine. */
export const CATEGORY_ORDER: { key: string; title: string }[] = [
	{ key: 'factual_accuracy', title: 'Factual accuracy' },
	{ key: 'completeness', title: 'Completeness' },
	{ key: 'evidence_traceability', title: 'Evidence & traceability' },
	{ key: 'reasoning_causal', title: 'Reasoning' },
	{ key: 'requirements_compliance', title: 'Requirements compliance' },
	{ key: 'critical_issue_detection', title: 'Critical-issue detection' },
	{ key: 'precision_specificity', title: 'Precision' },
	{ key: 'internal_consistency', title: 'Internal consistency' },
	{ key: 'actionability', title: 'Actionability' },
	{ key: 'communication_quality', title: 'Communication' }
];

/**
 * Claim classifications in the order they are shown. Verified first, then the
 * degrees of doubt: a reader scanning the row should see what was actually
 * established before what was only asserted.
 */
export const CLASSIFICATION_ORDER: { classification: ClaimClassification; title: string }[] = [
	{ classification: 'VERIFIED', title: 'verified' },
	{ classification: 'PARTIALLY_VERIFIED', title: 'partly verified' },
	{ classification: 'UNSUPPORTED', title: 'unsupported' },
	{ classification: 'CONTRADICTED', title: 'contradicted' },
	{ classification: 'FABRICATED_OR_HALLUCINATED', title: 'fabricated' },
	{ classification: 'NOT_VERIFIABLE', title: 'not verifiable' }
];

export const providerName = (provider: GeneratorId | string): string =>
	PROVIDER_LABELS[provider as GeneratorId] ?? provider;

export const confidenceWords = (level: string): string => level;

export interface VerdictLine {
	kind: VerdictKind;
	providers: GeneratorId[];
}

export const verdictLine = (adjudication: MappedAdjudication): VerdictLine => {
	if (adjudication.overall_winner) {
		return { kind: 'winner', providers: [adjudication.overall_winner] };
	}
	if (adjudication.tied_providers.length > 0) {
		return { kind: 'tie', providers: [...adjudication.tied_providers] };
	}
	return { kind: 'no_reliable_winner', providers: [] };
};

export interface ScoreRow {
	provider: GeneratorId;
	score: number;
	winner: boolean;
	integrityCapped: boolean;
}

/**
 * Score rows in the server's ranking order, so the page cannot present an
 * ordering the adjudication did not reach.
 */
export const scoreRows = (adjudication: MappedAdjudication): ScoreRow[] => {
	const capped = new Map(
		adjudication.candidate_scores.map((entry) => [entry.provider, entry.integrity_capped])
	);
	const winners = new Set<GeneratorId>(
		adjudication.overall_winner ? [adjudication.overall_winner] : adjudication.tied_providers
	);

	const ranked = adjudication.overall_ranking.filter((provider) => provider in adjudication.scores);
	// Anything the ranking somehow omits still gets a row: a provider that was
	// scored but not ranked must not vanish from the page.
	const missing = GENERATOR_IDS.filter(
		(provider) => provider in adjudication.scores && !ranked.includes(provider)
	);

	return [...ranked, ...missing].map((provider) => ({
		provider,
		score: adjudication.scores[provider] ?? 0,
		winner: winners.has(provider),
		integrityCapped: capped.get(provider) ?? false
	}));
};

export interface CategoryCell {
	provider: GeneratorId;
	score: number;
	best: boolean;
}

export interface CategoryRow {
	key: string;
	title: string;
	weight: number;
	cells: CategoryCell[];
	winners: GeneratorId[];
}

export const categoryRows = (adjudication: MappedAdjudication): CategoryRow[] => {
	const order = scoreRows(adjudication).map((row) => row.provider);

	return CATEGORY_ORDER.map(({ key, title }) => {
		const winners = adjudication.category_winners[key] ?? [];
		return {
			key,
			title,
			weight: adjudication.category_weights[key] ?? 0,
			winners: [...winners],
			cells: order.map((provider) => ({
				provider,
				score: adjudication.category_scores[provider]?.[key] ?? 0,
				best: winners.includes(provider)
			}))
		};
	});
};

export interface ClaimCount {
	classification: ClaimClassification;
	title: string;
	value: number;
}

export interface ClaimRow {
	provider: GeneratorId;
	counts: ClaimCount[];
	accuracyRatio: number | null;
	criticalCoverage: number | null;
	penaltyTotal: number;
}

/**
 * Per-provider claim counts and the two ratios.
 *
 * The ratios stay null rather than becoming 0 when they are undefined: "nothing
 * could be checked" and "nothing checked out" are opposite findings, and a
 * zero here would render the first as the second.
 */
export const claimRows = (adjudication: MappedAdjudication): ClaimRow[] =>
	scoreRows(adjudication).map((row) => {
		const summary = adjudication.claim_validation_summary[row.provider];
		const scored = adjudication.candidate_scores.find((entry) => entry.provider === row.provider);

		return {
			provider: row.provider,
			counts: CLASSIFICATION_ORDER.map(({ classification, title }) => ({
				classification,
				title,
				value: summary?.material_by_classification?.[classification] ?? 0
			})).filter((count) => count.value > 0),
			accuracyRatio: scored?.accuracy_confidence_ratio ?? null,
			criticalCoverage: scored?.critical_coverage_rate ?? null,
			penaltyTotal: scored?.penalty_total ?? 0
		};
	});
