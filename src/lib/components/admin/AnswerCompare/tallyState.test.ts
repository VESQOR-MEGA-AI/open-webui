import { describe, expect, it } from 'vitest';
import type { JudgeReports, ProviderId, ReportRow, Tally } from '$lib/apis/answer-compare';
import { AGREEMENT_LINE, applyTally, clearTally, EXCLUSION_COPY, tallyView } from './tallyState';

const baseTally = (overrides: Partial<Tally> = {}): Tally => ({
	current_versions: [
		{ provider: 'chatgpt', revision: 1 },
		{ provider: 'gemini', revision: 1 },
		{ provider: 'vesqor', revision: 1 }
	],
	included_reports: [
		{ judge: 'chatgpt', revision: 1 },
		{ judge: 'gemini', revision: 1 },
		{ judge: 'vesqor', revision: 1 }
	],
	included_judges: ['chatgpt', 'gemini', 'vesqor'],
	excluded: [],
	partial: false,
	verdicts: [
		{ judge: 'chatgpt', kind: 'winner', providers: ['vesqor'] },
		{ judge: 'gemini', kind: 'winner', providers: ['vesqor'] },
		{ judge: 'vesqor', kind: 'winner', providers: ['chatgpt'] }
	],
	votes: { chatgpt: 1, gemini: 0, vesqor: 2 },
	n_included: 3,
	n_judges: 3,
	outcome: { kind: 'preferred', provider: 'vesqor' },
	ties: [],
	inconclusive: [],
	self_votes: [],
	self_votes_excluded: [],
	...overrides
});

/** A complete report row whose mapped verdict names `winner`. */
const reportRow = (judge: ReportRow['judge'], winner: ProviderId): ReportRow => ({
	id: `${judge}-1`,
	run_id: 'run-1',
	judge,
	revision: 1,
	status: 'complete',
	requested_model: null,
	model: null,
	label_map: { A: 'chatgpt', B: 'gemini', C: 'vesqor' },
	report: null,
	judged_versions: null,
	blinding_compromised: [],
	missing_providers: [],
	params: null,
	mapped: {
		answers: {},
		verdict: { kind: 'winner', providers: [winner] },
		rationale: 'x',
		needs_verification: []
	},
	mapping_error: null,
	error: null
});

describe('the panel never counts', () => {
	it('shows the tally the server sent even when the reports in the same response disagree', () => {
		// Every report says VESQOR won; the tally says ChatGPT has the votes. A
		// panel that recounted from the reports would show VESQOR 3. The tally
		// is authoritative, so the panel must show exactly its numbers.
		const reports: JudgeReports[] = (['chatgpt', 'gemini', 'vesqor'] as const).map((judge) => ({
			judge,
			current: reportRow(judge, 'vesqor'),
			latest_attempt: reportRow(judge, 'vesqor'),
			outdated: false,
			capable: true
		}));
		const disagreeing = baseTally({
			votes: { chatgpt: 2, gemini: 0, vesqor: 0 },
			n_included: 2,
			partial: true,
			included_judges: ['chatgpt', 'gemini'],
			outcome: { kind: 'preferred', provider: 'chatgpt' }
		});

		const state = applyTally({ reports, tally: disagreeing });
		expect(state.tally).toBe(disagreeing);

		const view = tallyView(state.tally!);
		expect(view.headline).toEqual({
			key: 'Preferred answer: {{provider}} ({{votes}} of {{nIncluded}} judges)',
			params: { provider: 'ChatGPT', votes: 2, nIncluded: 2 }
		});
		expect(view.votes.map((v) => [v.provider, v.votes])).toEqual([
			['chatgpt', 2],
			['gemini', 0],
			['vesqor', 0]
		]);
		expect(view.partial?.params).toEqual({ nIncluded: 2, total: 3 });
	});

	it('is cleared when a response carries no tally', () => {
		expect(applyTally({ tally: baseTally() }).tally).not.toBeNull();
		expect(applyTally({}).tally).toBeNull();
		expect(clearTally().tally).toBeNull();
	});
});

describe('headline', () => {
	it('uses n_included as the second number, never 3', () => {
		const one = baseTally({
			votes: { chatgpt: 0, gemini: 0, vesqor: 1 },
			n_included: 1,
			partial: true,
			included_judges: ['chatgpt'],
			outcome: { kind: 'preferred', provider: 'vesqor' }
		});
		expect(tallyView(one).headline.params).toEqual({ provider: 'VESQOR', votes: 1, nIncluded: 1 });
	});

	it('a single independent verdict is a preferred answer, "1 of 1", with no two-judge sentence (DECISIONS.md#016)', () => {
		// Inverted from #014: the server now returns `preferred` for one verdict, and
		// the panel has no sentence explaining a floor that no longer exists.
		const lone = baseTally({
			n_included: 1,
			n_judges: 1,
			partial: false,
			included_judges: ['sonnet'],
			included_reports: [{ judge: 'sonnet', revision: 1 }],
			votes: { chatgpt: 0, gemini: 0, vesqor: 1 },
			verdicts: [{ judge: 'sonnet', kind: 'winner', providers: ['vesqor'] }],
			outcome: { kind: 'preferred', provider: 'vesqor' }
		});
		const view = tallyView(lone);
		expect(view.headline.key).toBe(
			'Preferred answer: {{provider}} ({{votes}} of {{nIncluded}} judges)'
		);
		expect(view.headline.params).toEqual({ provider: 'VESQOR', votes: 1, nIncluded: 1 });
		expect(view.partial).toBeNull();
		expect(JSON.stringify(view)).not.toContain('requires at least two');

		// A lone tie is honestly "No majority", with the same plain wording as for many.
		const loneTie = baseTally({
			n_included: 1,
			n_judges: 1,
			votes: { chatgpt: 0, gemini: 0, vesqor: 0 },
			verdicts: [{ judge: 'sonnet', kind: 'tie', providers: ['chatgpt', 'vesqor'] }],
			ties: [{ judge: 'sonnet', providers: ['chatgpt', 'vesqor'] }],
			outcome: { kind: 'no_majority', provider: null }
		});
		expect(tallyView(loneTie).headline.key).toBe('No majority');
		expect(tallyView(loneTie).verdicts[0].text?.params).toEqual({
			judge: 'Sonnet',
			providers: 'ChatGPT, VESQOR'
		});
	});

	it('names the two other outcomes plainly', () => {
		expect(
			tallyView(baseTally({ outcome: { kind: 'no_majority', provider: null } })).headline.key
		).toBe('No majority');
		expect(
			tallyView(
				baseTally({ outcome: { kind: 'no_valid_verdicts', provider: null }, n_included: 0 })
			).headline.key
		).toBe('No valid verdicts yet');
	});
});

describe('partial, excluded, ties, inconclusive, self-votes', () => {
	it('maps every exclusion reason to plain words and carries the failed re-judge', () => {
		const view = tallyView(
			baseTally({
				partial: true,
				n_included: 0,
				included_judges: [],
				verdicts: [],
				votes: { chatgpt: 0, gemini: 0, vesqor: 0 },
				outcome: { kind: 'no_valid_verdicts', provider: null },
				excluded: [
					{ judge: 'chatgpt', reason: 'outdated', latest_attempt: 'malformed' },
					{ judge: 'gemini', reason: 'not_configured' },
					{ judge: 'vesqor', reason: 'no_report' }
				],
				self_votes_excluded: [{ judge: 'chatgpt', kind: 'winner' }]
			})
		);
		expect(view.partial?.key).toBe('Partial: {{nIncluded}} of {{total}} judges included');
		expect(view.partial?.params).toEqual({ nIncluded: 0, total: 3 });
		expect(view.excluded.map((e) => [e.name, e.reason.key])).toEqual([
			['ChatGPT', 'report is outdated (answers changed)'],
			['Gemini', 'requires configuration'],
			['VESQOR', 'not judged']
		]);
		expect(view.excluded[0].latestAttempt?.key).toBe('latest re-judge could not be read');
		expect(view.excluded[0].selfVote?.key).toBe('{{judge}} judged its own answer the winner');
		expect(view.excluded[1].selfVote).toBeNull();
	});

	it('every backend exclusion reason has copy', () => {
		for (const reason of [
			'no_report',
			'outdated',
			'failed',
			'malformed',
			'not_configured',
			'not_capable',
			'unmappable'
		]) {
			expect(EXCLUSION_COPY[reason], reason).toBeTruthy();
		}
	});

	it("the partial denominator is the server's judge count, never the number of answer providers", () => {
		const view = tallyView(
			baseTally({
				partial: true,
				n_included: 0,
				n_judges: 1,
				included_judges: [],
				verdicts: [],
				votes: { chatgpt: 0, gemini: 0, vesqor: 0 },
				outcome: { kind: 'no_valid_verdicts', provider: null },
				excluded: [{ judge: 'sonnet', reason: 'no_report' }]
			})
		);
		expect(view.partial?.params).toEqual({ nIncluded: 0, total: 1 });
		expect(view.excluded.map((e) => [e.name, e.reason.key])).toEqual([['Sonnet', 'not judged']]);
	});

	it('a legacy participant judge is listed as excluded with its own words, beside the independent judge', () => {
		const view = tallyView(
			baseTally({
				partial: false,
				n_included: 1,
				n_judges: 1,
				included_judges: ['sonnet'],
				verdicts: [{ judge: 'sonnet', kind: 'winner', providers: ['gemini'] }],
				votes: { chatgpt: 0, gemini: 1, vesqor: 0 },
				outcome: { kind: 'preferred', provider: 'gemini' },
				excluded: [{ judge: 'chatgpt', reason: 'not_capable' }]
			})
		);
		expect(view.partial).toBeNull();
		expect(view.excluded.map((e) => [e.name, e.reason.key])).toEqual([
			['ChatGPT', 'participant judge, no longer used']
		]);
		// Votes stay per answer provider: the judge is never a column.
		expect(view.votes.map((v) => v.provider)).toEqual(['chatgpt', 'gemini', 'vesqor']);
	});

	it('says ties and no-reliable-winner in plain words, per judge', () => {
		const view = tallyView(
			baseTally({
				verdicts: [
					{ judge: 'chatgpt', kind: 'tie', providers: ['chatgpt', 'vesqor'] },
					{ judge: 'gemini', kind: 'no_reliable_winner', providers: [] },
					{ judge: 'vesqor', kind: 'winner', providers: ['chatgpt'] }
				],
				ties: [{ judge: 'chatgpt', providers: ['chatgpt', 'vesqor'] }],
				inconclusive: ['gemini'],
				self_votes: [{ judge: 'chatgpt', kind: 'tie' }],
				votes: { chatgpt: 1, gemini: 0, vesqor: 0 },
				outcome: { kind: 'no_majority', provider: null }
			})
		);
		const byJudge = Object.fromEntries(view.verdicts.map((v) => [v.judge, v]));
		expect(byJudge.chatgpt.text).toEqual({
			key: '{{judge}} judged a tie between {{providers}}.',
			params: { judge: 'ChatGPT', providers: 'ChatGPT, VESQOR' }
		});
		expect(byJudge.gemini.text).toEqual({
			key: '{{judge}} found no reliable winner.',
			params: { judge: 'Gemini' }
		});
		expect(byJudge.vesqor.text).toBeNull();
		// The self-vote is an annotation beside the verdict, not a change to the count.
		expect(byJudge.chatgpt.selfVote?.key).toBe('{{judge}} included its own answer in a tie');
		expect(view.votes.find((v) => v.provider === 'chatgpt')?.votes).toBe(1);
	});

	it('annotates a winner self-vote without touching the displayed votes', () => {
		const flagged = baseTally({ self_votes: [{ judge: 'vesqor', kind: 'winner' }] });
		const view = tallyView(flagged);
		expect(view.verdicts.find((v) => v.judge === 'vesqor')?.selfVote?.key).toBe(
			'{{judge}} judged its own answer the winner'
		);
		// "Annotate, never change the count" holds on screen too: the displayed
		// votes are exactly the server's, self-vote or not.
		const shown = Object.fromEntries(view.votes.map((v) => [v.provider, v.votes]));
		expect(shown).toEqual(baseTally().votes);
		expect(shown).toEqual(flagged.votes);
		expect(view.headline.params.votes).toBe(flagged.votes.vesqor);
	});

	it('always ends with the agreement line', () => {
		expect(tallyView(baseTally()).footer.key).toBe(AGREEMENT_LINE);
		expect(AGREEMENT_LINE).toBe('Agreement between judges is not evidence of correctness.');
	});
});
