import { describe, expect, it } from 'vitest';
import type { JudgeCardsState } from './judgeState';
import type { ProviderConfig, SummaryRow, Tally } from '$lib/apis/answer-compare';
import { providersToGenerate } from './state';
import { applyJudgeFailure, applyJudgeNotConfigured, initialJudgeCards, judgesToRun, startJudging } from './judgeState';
import {
	applySummary,
	applySummaryRow,
	clearSummary,
	initialSummaryState,
	NO_VERDICTS_COPY,
	OUTDATED_COPY,
	summaryButtonDisabledReason,
	summaryView
} from './summaryState';

const NARRATIVE = `OUTCOME

No majority among 2 valid verdicts.

VERDICTS

ChatGPT: tie — ChatGPT (was A), VESQOR (was B).
Answer A and Answer B are close.
A is crisper; B is more complete.

Agreement between judges is not evidence of correctness.
`;

const summaryRow = (overrides: Partial<SummaryRow> = {}): SummaryRow => ({
	id: 'summary-1',
	run_id: 'run-1',
	revision: 2,
	narrative: NARRATIVE,
	tally: {} as Tally,
	judged_versions: [{ provider: 'chatgpt', revision: 1 }],
	partial: true,
	included_judges: ['chatgpt', 'vesqor'],
	outdated: false,
	...overrides
});

const config = (id: ProviderConfig['id'], configured: boolean): ProviderConfig => ({
	id,
	configured,
	missing: configured ? [] : [`ANSWER_COMPARE_${id.toUpperCase()}_API_KEY`],
	base_url: 'https://x',
	model: configured ? 'm' : null
});

describe('the panel renders the narrative and never rewrites it', () => {
	it('passes the narrative through untouched, by identity', () => {
		const row = summaryRow();
		const state = applySummary({ summary: { current: row, revisions: 3 } });

		expect(state.summary).toBe(row);
		const view = summaryView(state.summary!);
		// Identity, not equality: a module that reflowed or trimmed would fail here.
		expect(view.narrative).toBe(row.narrative);
		expect(view.narrative).toBe(NARRATIVE);
		expect(view.narrative.split('\n').length).toBe(NARRATIVE.split('\n').length);
	});

	it('carries the version and the revision count', () => {
		const state = applySummary({ summary: { current: summaryRow(), revisions: 3 } });
		expect(state.revisions).toBe(3);
		expect(summaryView(state.summary!).version).toEqual({ key: 'Version {{revision}}', params: { revision: 2 } });
	});

	it('shows the outdated banner only when the server says so', () => {
		expect(summaryView(summaryRow({ outdated: true })).outdated?.key).toBe(OUTDATED_COPY);
		expect(summaryView(summaryRow({ outdated: false })).outdated).toBeNull();
		expect(OUTDATED_COPY).toBe(
			'Built on earlier answers or earlier reports. Build again to summarise the current state.'
		);
	});

	it('is empty when a response carries no summary, and clears on demand', () => {
		expect(initialSummaryState()).toEqual({ summary: null, revisions: 0 });
		expect(applySummary({}).summary).toBeNull();
		expect(applySummary({ summary: { current: null, revisions: 0 } }).summary).toBeNull();
		expect(clearSummary()).toEqual({ summary: null, revisions: 0 });
	});

	it('adopts a freshly posted row without losing the revision count', () => {
		const state = applySummary({ summary: { current: summaryRow({ revision: 1 }), revisions: 1 } });
		const posted = summaryRow({ revision: 2 });
		const after = applySummaryRow(state, posted);

		expect(after.summary).toBe(posted);
		expect(after.revisions).toBe(2);
	});

	it('disables the button only with no valid verdict', () => {
		expect(summaryButtonDisabledReason(0)?.key).toBe(NO_VERDICTS_COPY);
		expect(summaryButtonDisabledReason(1)).toBeNull();
		expect(summaryButtonDisabledReason(3)).toBeNull();
	});
});

describe('the cost dialog counts what will actually be sent', () => {
	it('counts only configured providers for generate', () => {
		expect(providersToGenerate([config('chatgpt', true), config('gemini', true), config('vesqor', true)])).toEqual([
			'chatgpt',
			'gemini',
			'vesqor'
		]);
		expect(providersToGenerate([config('chatgpt', true), config('gemini', false), config('vesqor', true)])).toEqual([
			'chatgpt',
			'vesqor'
		]);
		expect(providersToGenerate([])).toEqual([]);
	});

	it('counts only judges that will really be called', () => {
		let cards: JudgeCardsState = initialJudgeCards();
		expect(judgesToRun(cards)).toEqual(['chatgpt', 'gemini', 'vesqor']);

		// Unconfigured: the server would skip it, so it is not a paid request.
		cards = applyJudgeNotConfigured(cards, 'gemini', ['ANSWER_COMPARE_GEMINI_MODEL']);
		expect(judgesToRun(cards)).toEqual(['chatgpt', 'vesqor']);

		// Already running: skipped too.
		cards = startJudging(cards, 'chatgpt', 1000);
		expect(judgesToRun(cards)).toEqual(['vesqor']);

		// Oversized would be skipped as well; an ordinary failure would not.
		const oversized = applyJudgeFailure(initialJudgeCards(), 'vesqor', { code: 'oversized' });
		expect(judgesToRun(oversized)).toEqual(['chatgpt', 'gemini']);
		const timedOut = applyJudgeFailure(initialJudgeCards(), 'vesqor', { code: 'timeout' });
		expect(judgesToRun(timedOut)).toEqual(['chatgpt', 'gemini', 'vesqor']);
	});
});
