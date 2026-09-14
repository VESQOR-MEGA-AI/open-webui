import { describe, expect, it } from 'vitest';
import type { JudgeReports, ReportRow, Tally } from '$lib/apis/answer-compare';
import { AGREEMENT_LINE, applyTally, clearTally, EXCLUSION_COPY, SINGLE_JUDGE_COPY, tallyView } from './tallyState';

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
	outcome: { kind: 'preferred', provider: 'vesqor' },
	ties: [],
	inconclusive: [],
	self_votes: [],
	self_votes_excluded: [],
	...overrides
});

/** A complete report row whose mapped verdict names `winner`. */
const reportRow = (judge: ReportRow['judge'], winner: ReportRow['judge']): ReportRow => ({
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
			outdated: false
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

	it('explains why a single verdict yields no preferred answer', () => {
		const lone = baseTally({
			n_included: 1,
			partial: true,
			included_judges: ['chatgpt'],
			votes: { chatgpt: 0, gemini: 0, vesqor: 1 },
			verdicts: [{ judge: 'chatgpt', kind: 'winner', providers: ['vesqor'] }],
			outcome: { kind: 'no_majority', provider: null }
		});
		expect(tallyView(lone).headline.key).toBe(SINGLE_JUDGE_COPY);
		expect(SINGLE_JUDGE_COPY).toBe(
			'Only one judge has a valid verdict — a preferred answer requires at least two.'
		);
		// Two or more keeps the plain wording.
		expect(tallyView(baseTally({ n_included: 2, outcome: { kind: 'no_majority', provider: null } })).headline.key).toBe(
			'No majority'
		);
	});

	it('names the two other outcomes plainly', () => {
		expect(tallyView(baseTally({ outcome: { kind: 'no_majority', provider: null } })).headline.key).toBe('No majority');
		expect(
			tallyView(baseTally({ outcome: { kind: 'no_valid_verdicts', provider: null }, n_included: 0 })).headline.key
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
		for (const reason of ['no_report', 'outdated', 'failed', 'malformed', 'not_configured', 'unmappable']) {
			expect(EXCLUSION_COPY[reason], reason).toBeTruthy();
		}
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
		expect(byJudge.gemini.text).toEqual({ key: '{{judge}} found no reliable winner.', params: { judge: 'Gemini' } });
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
