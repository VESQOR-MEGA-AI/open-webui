import { describe, expect, it } from 'vitest';
import type { JudgeReports, MappedReport, ProviderConfig, RawReport, ReportRow } from '$lib/apis/answer-compare';
import { ERROR_COPY } from './state';
import {
	answerSections,
	emptyJudgeCard,
	type JudgeCardState,
	type JudgeCardsState,
	applyJudgeConfigs,
	applyJudgeFailure,
	applyJudgeNotConfigured,
	applyJudgeNotEnoughAnswers,
	applyJudgeOversized,
	applyJudgeResult,
	applyJudgeRunState,
	describeJudgeFailure,
	initialJudgeCards,
	isJudgeRetryDisabled,
	JUDGE_ERROR_COPY,
	judgeButtonDisabledReason,
	judgePhase,
	labelsByProvider,
	nameWithLabel,
	startJudging,
	verdictHeadline
} from './judgeState';

const LABEL_MAP = { A: 'vesqor', B: 'chatgpt', C: 'gemini' } as const;

const section = (label: string, note: string) => ({
	label,
	strengths: [{ passage: `quoted from ${label}`, note }],
	errors_or_unsupported: [],
	omissions: [{ passage: '', note: 'a general\nmulti-line remark' }],
	useful_extras: [],
	unnecessary: [],
	improvements: []
});

const rawReport = (): RawReport => ({
	answers: [section('A', 'A is tight'), section('B', 'B is verbose'), section('C', 'C is fine')],
	verdict: { kind: 'winner', labels: ['A'] },
	rationale: 'Answer A is more accurate.\n\nAnswer B pads.',
	needs_verification: ['the p99 claim']
});

const mappedReport = (): MappedReport => ({
	answers: {
		vesqor: section('A', 'A is tight'),
		chatgpt: section('B', 'B is verbose'),
		gemini: section('C', 'C is fine')
	},
	verdict: { kind: 'winner', providers: ['vesqor'] },
	rationale: 'Answer A is more accurate.\n\nAnswer B pads.',
	needs_verification: ['the p99 claim']
});

const reportRow = (overrides: Partial<ReportRow> = {}): ReportRow => ({
	id: 'report-1',
	run_id: 'run-1',
	judge: 'anthropic',
	revision: 1,
	status: 'complete',
	requested_model: 'claude-sonnet-5',
	model: 'claude-sonnet-5',
	label_map: { ...LABEL_MAP },
	report: rawReport(),
	judged_versions: [
		{ provider: 'chatgpt', revision: 1 },
		{ provider: 'gemini', revision: 1 },
		{ provider: 'vesqor', revision: 1 }
	],
	blinding_compromised: [],
	missing_providers: [],
	params: { structured_output_mode: 1 },
	mapped: mappedReport(),
	mapping_error: null,
	error: null,
	...overrides
});

const config = (overrides: Partial<ProviderConfig> = {}): ProviderConfig => ({
	id: 'anthropic',
	configured: true,
	missing: [],
	base_url: 'https://x',
	model: 'claude-sonnet-5',
	can_judge: true,
	...overrides
});

const withReport = () => applyJudgeResult(initialJudgeCards(), 'anthropic', reportRow());

/**
 * A hand-built panel of three.
 *
 * The product's panel is one independent adjudicator, so there are no siblings
 * left for the real state to preserve — but every transition in `judgeState.ts`
 * is still written to a panel of any size, and sibling identity is the rule
 * that keeps it that way. Building the wider panel here exercises that
 * mechanism directly instead of letting it go untested the day the panel
 * became a single card.
 */
const SECOND = 'second' as never;
const THIRD = 'third' as never;
const widePanel = (base: JudgeCardsState): JudgeCardsState =>
	({
		...base,
		[SECOND]: { ...emptyJudgeCard('anthropic'), judge: SECOND } as JudgeCardState,
		[THIRD]: { ...emptyJudgeCard('anthropic'), judge: THIRD } as JudgeCardState
	}) as JudgeCardsState;

describe('judge-card independence', () => {
	it('leaves the other judges identical by reference on a failure', () => {
		const before = widePanel(initialJudgeCards());
		const after = applyJudgeFailure(before, SECOND, { code: 'timeout' });

		expect(after.anthropic).toBe(before.anthropic);
		expect(after[THIRD]).toBe(before[THIRD]);
		expect(after[SECOND]).not.toBe(before[SECOND]);
	});

	it('leaves the other judges identical on every transition', () => {
		const before = widePanel(withReport());
		const transitions = [
			() => startJudging(before, SECOND, 1000),
			() => applyJudgeNotConfigured(before, SECOND, ['ANSWER_COMPARE_ANTHROPIC_MODEL']),
			() => applyJudgeOversized(before, SECOND, 400000, 500000),
			() => applyJudgeNotEnoughAnswers(before, SECOND, ['gemini']),
			() => applyJudgeFailure(before, SECOND, { code: 'malformed_report' }),
			() => applyJudgeResult(before, SECOND, reportRow({ judge: SECOND })),
			() => applyJudgeResult(before, SECOND, reportRow({ judge: SECOND, status: 'pending', report: null, mapped: null }))
		];
		for (const transition of transitions) {
			const after = transition();
			expect(after.anthropic).toBe(before.anthropic);
			expect(after[THIRD]).toBe(before[THIRD]);
		}
	});
});

describe('a report is removed by nothing but a successful new version', () => {
	it('keeps the report and adds the failure when a re-judge fails', () => {
		const before = withReport();
		const after = applyJudgeResult(
			before,
			'anthropic',
			reportRow({ id: 'report-2', revision: 2, status: 'failed', report: null, mapped: null, error: { code: 'malformed_report', message: 'x' } })
		);

		expect(after.anthropic.report).toBe(before.anthropic.report);
		expect(after.anthropic.failure).toEqual({ code: 'malformed_report' });
		expect(judgePhase(after.anthropic)).toBe('failed');
	});

	it('keeps the report, by identity, on every failure path the page takes', () => {
		// The page reaches applyJudgeFailure from four places (a typed backend
		// error, exhausted recovery -> network, unauthorized, the generic catch),
		// none of which go through applyJudgeResult. Same rule, same guard.
		const before = withReport();
		for (const code of ['network', 'unauthorized', 'auth', 'malformed_report', 'teapot']) {
			const after = applyJudgeFailure(before, 'anthropic', { code });
			expect(after.anthropic.report).toBe(before.anthropic.report);
			expect(after.anthropic.failure).toEqual({ code });
			expect(after.anthropic.inFlight).toBe(false);
			expect(judgePhase(after.anthropic)).toBe('failed');
		}
		const oversized = applyJudgeOversized(before, 'anthropic', 400000, 500000);
		expect(oversized.anthropic.report).toBe(before.anthropic.report);
		const notEnough = applyJudgeNotEnoughAnswers(before, 'anthropic', ['chatgpt']);
		expect(notEnough.anthropic.report).toBe(before.anthropic.report);
	});

	it('does not clear the existing report when a re-judge starts', () => {
		const before = withReport();
		const after = startJudging(before, 'anthropic', 5000);

		expect(after.anthropic.report).toBe(before.anthropic.report);
		expect(judgePhase(after.anthropic)).toBe('rejudging');
		expect(judgePhase(startJudging(initialJudgeCards(), 'anthropic', 5000).anthropic)).toBe('judging');
	});

	it('keeps a rendered report when the server has no complete row for it', () => {
		const before = withReport();
		const reports: JudgeReports[] = [
			{
				judge: 'anthropic',
				current: null,
				latest_attempt: reportRow({ id: 'report-2', revision: 2, status: 'failed', report: null, mapped: null, error: { code: 'stale', message: 'x' } }),
				outdated: false
			}
		];
		const after = applyJudgeRunState(before, reports);

		expect(after.anthropic.report).toBe(before.anthropic.report);
		expect(after.anthropic.failure).toEqual({ code: 'stale' });
	});

	it('keeps the rendered report when the server reports nothing at all', () => {
		const before = withReport();
		const after = applyJudgeRunState(before, [{ judge: 'anthropic', current: null, latest_attempt: null, outdated: false }]);
		expect(after.anthropic.report).toBe(before.anthropic.report);
		expect(judgePhase(after.anthropic)).toBe('complete');
	});

	it('replaces the report only with a newer complete one', () => {
		const before = withReport();
		const newer = reportRow({ id: 'report-3', revision: 3 });
		const after = applyJudgeResult(before, 'anthropic', newer);
		expect(after.anthropic.report).toBe(newer);
		expect(after.anthropic.failure).toBeNull();
	});
});

describe('outdated', () => {
	it('is set from the server and cleared by a later false', () => {
		const entry = (outdated: boolean): JudgeReports => ({
			judge: 'anthropic',
			current: reportRow(),
			latest_attempt: reportRow(),
			outdated
		});
		const flagged = applyJudgeRunState(initialJudgeCards(), [entry(true)]);
		expect(flagged.anthropic.outdated).toBe(true);

		const cleared = applyJudgeRunState(flagged, [entry(false)]);
		expect(cleared.anthropic.outdated).toBe(false);
	});

	it('is never true without a current report', () => {
		const after = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'anthropic', current: null, latest_attempt: null, outdated: true }
		]);
		expect(after.anthropic.outdated).toBe(false);
	});

	it('is not inherited by a new complete report', () => {
		const flagged = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'anthropic', current: reportRow(), latest_attempt: reportRow(), outdated: true }
		]);
		expect(flagged.anthropic.outdated).toBe(true);

		const rejudged = applyJudgeResult(flagged, 'anthropic', reportRow({ id: 'report-2', revision: 2 }));
		expect(rejudged.anthropic.outdated).toBe(false);
		expect(rejudged.anthropic.report?.revision).toBe(2);
	});
});

describe('display substitution — not mapping', () => {
	it('inverts label_map into "(was X)" per provider', () => {
		expect(labelsByProvider(LABEL_MAP)).toEqual({ vesqor: 'A', chatgpt: 'B', gemini: 'C' });
		expect(nameWithLabel('chatgpt', LABEL_MAP)).toEqual({ provider: 'chatgpt', name: 'ChatGPT', label: 'B' });
		expect(nameWithLabel('chatgpt', null).label).toBeNull();
	});

	it('renders a tie with every tied provider and its label', () => {
		const mapped: MappedReport = { ...mappedReport(), verdict: { kind: 'tie', providers: ['chatgpt', 'vesqor'] } };
		const headline = verdictHeadline(mapped, LABEL_MAP);
		expect(headline.kind).toBe('tie');
		expect(headline.providers).toEqual([
			{ provider: 'chatgpt', name: 'ChatGPT', label: 'B' },
			{ provider: 'vesqor', name: 'VESQOR', label: 'A' }
		]);
	});

	it('passes the judge\'s text through untouched, by identity', () => {
		const mapped = mappedReport();
		const sections = answerSections(mapped, LABEL_MAP);

		expect(sections.map((s) => s.named.provider)).toEqual(['chatgpt', 'gemini', 'vesqor']);
		const chatgpt = sections[0];
		// Identity: the very objects the server sent, not copies with edits.
		expect(chatgpt.lists[0].items[0]).toBe(mapped.answers.chatgpt!.strengths[0]);
		expect(chatgpt.lists[0].items[0].note).toBe('B is verbose');
		expect(chatgpt.lists[0].items[0].passage).toBe('quoted from B');
		expect(chatgpt.lists[1].items[0].note).toBe('a general\nmulti-line remark');
		expect(mapped.rationale).toBe('Answer A is more accurate.\n\nAnswer B pads.');
	});

	it('drops empty lists and keeps the fixed order of the rest', () => {
		const sections = answerSections(mappedReport(), LABEL_MAP);
		for (const s of sections) {
			expect(s.lists.map((l) => l.field)).toEqual(['strengths', 'omissions']);
			expect(s.lists.map((l) => l.title)).toEqual(['Strengths', 'Important omissions']);
		}
	});
});

describe('copy and controls', () => {
	it('gives malformed_report its own line, not the generic one', () => {
		const described = describeJudgeFailure({ code: 'malformed_report' });
		expect(described.key).toBe(JUDGE_ERROR_COPY.malformed_report);
		expect(described.key).not.toContain('{{code}}');
	});

	it('falls back to the shared copy for client codes and keeps unknown codes visible', () => {
		expect(describeJudgeFailure({ code: 'timeout' }).key).toBe(ERROR_COPY.timeout);
		const unknown = describeJudgeFailure({ code: 'teapot' });
		expect(unknown.key).toContain('{{code}}');
		expect(unknown.params.code).toBe('teapot');
	});

	it('oversized copy is the judging one, carrying both numbers and the judge', () => {
		const after = applyJudgeOversized(initialJudgeCards(), 'anthropic', 400000, 512000);
		const described = describeJudgeFailure(after.anthropic.failure!);
		expect(described.key).toBe(JUDGE_ERROR_COPY.oversized);
		expect(described.params).toMatchObject({ judge: 'Claude', limitChars: 400000, actualChars: 512000 });
		expect(isJudgeRetryDisabled(after.anthropic)).toBe(true);
	});

	it('not_enough_answers names the available providers', () => {
		// The judge is the adjudicator; the names listed are the CANDIDATES that
		// do have an answer, which is what makes the message actionable.
		const after = applyJudgeNotEnoughAnswers(initialJudgeCards(), 'anthropic', ['gemini']);
		expect(describeJudgeFailure(after.anthropic.failure!).params.providers).toBe('Gemini');
	});

	it('button reason: unlock at two answers, then configuration, then running', () => {
		const cards = initialJudgeCards();
		expect(judgeButtonDisabledReason(cards.anthropic, 1)?.key).toBe('Judging unlocks once at least two answers exist.');
		expect(judgeButtonDisabledReason(cards.anthropic, 2)).toBeNull();

		const unconfigured = applyJudgeConfigs(cards, [config({ configured: false, missing: ['ANSWER_COMPARE_ANTHROPIC_BASE_URL'] })]);
		expect(judgeButtonDisabledReason(unconfigured.anthropic, 3)).toEqual({
			key: 'Requires configuration: {{missing}}',
			params: { missing: 'ANSWER_COMPARE_ANTHROPIC_BASE_URL' }
		});

		const running = startJudging(cards, 'anthropic', 1);
		expect(judgeButtonDisabledReason(running.anthropic, 3)?.key).toBe('This judge is already running.');
	});
});
