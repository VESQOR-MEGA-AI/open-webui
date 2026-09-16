import { describe, expect, it } from 'vitest';
import type { JudgeReports, MappedReport, ProviderConfig, RawReport, ReportRow } from '$lib/apis/answer-compare';
import { ERROR_COPY } from './state';
import {
	answerSections,
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
	reportToText,
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
	judge: 'gemini',
	revision: 1,
	status: 'complete',
	requested_model: 'gemini-test-1',
	model: 'gemini-test-1-002',
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
	id: 'gemini',
	configured: true,
	missing: [],
	base_url: 'https://x',
	model: 'gemini-test-1',
	...overrides
});

const withReport = () => applyJudgeResult(initialJudgeCards(), 'gemini', reportRow());

describe('judge-card independence', () => {
	it('leaves the other judges identical by reference on a failure', () => {
		const before = initialJudgeCards();
		const after = applyJudgeFailure(before, 'chatgpt', { code: 'timeout' });

		expect(after.gemini).toBe(before.gemini);
		expect(after.vesqor).toBe(before.vesqor);
		expect(after.chatgpt).not.toBe(before.chatgpt);
	});

	it('leaves the other judges identical on every transition', () => {
		const before = withReport();
		const transitions = [
			() => startJudging(before, 'chatgpt', 1000),
			() => applyJudgeNotConfigured(before, 'chatgpt', ['ANSWER_COMPARE_CHATGPT_MODEL']),
			() => applyJudgeOversized(before, 'chatgpt', 400000, 500000),
			() => applyJudgeNotEnoughAnswers(before, 'chatgpt', ['gemini']),
			() => applyJudgeFailure(before, 'chatgpt', { code: 'malformed_report' }),
			() => applyJudgeResult(before, 'chatgpt', reportRow({ judge: 'chatgpt' })),
			() => applyJudgeResult(before, 'chatgpt', reportRow({ judge: 'chatgpt', status: 'pending', report: null, mapped: null }))
		];
		for (const transition of transitions) {
			const after = transition();
			expect(after.gemini).toBe(before.gemini);
			expect(after.vesqor).toBe(before.vesqor);
		}
	});
});

describe('a report is removed by nothing but a successful new version', () => {
	it('keeps the report and adds the failure when a re-judge fails', () => {
		const before = withReport();
		const after = applyJudgeResult(
			before,
			'gemini',
			reportRow({ id: 'report-2', revision: 2, status: 'failed', report: null, mapped: null, error: { code: 'malformed_report', message: 'x' } })
		);

		expect(after.gemini.report).toBe(before.gemini.report);
		expect(after.gemini.failure).toEqual({ code: 'malformed_report' });
		expect(judgePhase(after.gemini)).toBe('failed');
	});

	it('keeps the report, by identity, on every failure path the page takes', () => {
		// The page reaches applyJudgeFailure from four places (a typed backend
		// error, exhausted recovery -> network, unauthorized, the generic catch),
		// none of which go through applyJudgeResult. Same rule, same guard.
		const before = withReport();
		for (const code of ['network', 'unauthorized', 'auth', 'malformed_report', 'teapot']) {
			const after = applyJudgeFailure(before, 'gemini', { code });
			expect(after.gemini.report).toBe(before.gemini.report);
			expect(after.gemini.failure).toEqual({ code });
			expect(after.gemini.inFlight).toBe(false);
			expect(judgePhase(after.gemini)).toBe('failed');
		}
		const oversized = applyJudgeOversized(before, 'gemini', 400000, 500000);
		expect(oversized.gemini.report).toBe(before.gemini.report);
		const notEnough = applyJudgeNotEnoughAnswers(before, 'gemini', ['chatgpt']);
		expect(notEnough.gemini.report).toBe(before.gemini.report);
	});

	it('does not clear the existing report when a re-judge starts', () => {
		const before = withReport();
		const after = startJudging(before, 'gemini', 5000);

		expect(after.gemini.report).toBe(before.gemini.report);
		expect(judgePhase(after.gemini)).toBe('rejudging');
		expect(judgePhase(startJudging(initialJudgeCards(), 'gemini', 5000).gemini)).toBe('judging');
	});

	it('keeps a rendered report when the server has no complete row for it', () => {
		const before = withReport();
		const reports: JudgeReports[] = [
			{
				judge: 'gemini',
				current: null,
				latest_attempt: reportRow({ id: 'report-2', revision: 2, status: 'failed', report: null, mapped: null, error: { code: 'stale', message: 'x' } }),
				outdated: false
			}
		];
		const after = applyJudgeRunState(before, reports);

		expect(after.gemini.report).toBe(before.gemini.report);
		expect(after.gemini.failure).toEqual({ code: 'stale' });
	});

	it('keeps the rendered report when the server reports nothing at all', () => {
		const before = withReport();
		const after = applyJudgeRunState(before, [{ judge: 'gemini', current: null, latest_attempt: null, outdated: false }]);
		expect(after.gemini.report).toBe(before.gemini.report);
		expect(judgePhase(after.gemini)).toBe('complete');
	});

	it('replaces the report only with a newer complete one', () => {
		const before = withReport();
		const newer = reportRow({ id: 'report-3', revision: 3 });
		const after = applyJudgeResult(before, 'gemini', newer);
		expect(after.gemini.report).toBe(newer);
		expect(after.gemini.failure).toBeNull();
	});
});

describe('outdated', () => {
	it('is set from the server and cleared by a later false', () => {
		const entry = (outdated: boolean): JudgeReports => ({
			judge: 'gemini',
			current: reportRow(),
			latest_attempt: reportRow(),
			outdated
		});
		const flagged = applyJudgeRunState(initialJudgeCards(), [entry(true)]);
		expect(flagged.gemini.outdated).toBe(true);

		const cleared = applyJudgeRunState(flagged, [entry(false)]);
		expect(cleared.gemini.outdated).toBe(false);
	});

	it('is never true without a current report', () => {
		const after = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'gemini', current: null, latest_attempt: null, outdated: true }
		]);
		expect(after.gemini.outdated).toBe(false);
	});

	it('is not inherited by a new complete report', () => {
		const flagged = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'gemini', current: reportRow(), latest_attempt: reportRow(), outdated: true }
		]);
		expect(flagged.gemini.outdated).toBe(true);

		const rejudged = applyJudgeResult(flagged, 'gemini', reportRow({ id: 'report-2', revision: 2 }));
		expect(rejudged.gemini.outdated).toBe(false);
		expect(rejudged.gemini.report?.revision).toBe(2);
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
		const after = applyJudgeOversized(initialJudgeCards(), 'vesqor', 400000, 512000);
		const described = describeJudgeFailure(after.vesqor.failure!);
		expect(described.key).toBe(JUDGE_ERROR_COPY.oversized);
		expect(described.params).toMatchObject({ judge: 'VESQOR', limitChars: 400000, actualChars: 512000 });
		expect(isJudgeRetryDisabled(after.vesqor)).toBe(true);
	});

	it('not_enough_answers names the available providers', () => {
		const after = applyJudgeNotEnoughAnswers(initialJudgeCards(), 'chatgpt', ['gemini']);
		expect(describeJudgeFailure(after.chatgpt.failure!).params.providers).toBe('Gemini');
	});

	it('button reason: unlock at two answers, then configuration, then running', () => {
		const cards = initialJudgeCards();
		expect(judgeButtonDisabledReason(cards.gemini, 1)?.key).toBe('Judging unlocks once at least two answers exist.');
		expect(judgeButtonDisabledReason(cards.gemini, 2)).toBeNull();

		const unconfigured = applyJudgeConfigs(cards, [config({ configured: false, missing: ['ANSWER_COMPARE_GEMINI_MODEL'] })]);
		expect(judgeButtonDisabledReason(unconfigured.gemini, 3)).toEqual({
			key: 'Requires configuration: {{missing}}',
			params: { missing: 'ANSWER_COMPARE_GEMINI_MODEL' }
		});

		const running = startJudging(cards, 'gemini', 1);
		expect(judgeButtonDisabledReason(running.gemini, 3)?.key).toBe('This judge is already running.');
	});
});

describe('reportToText — the Copy button', () => {
	it('leads with the judge and the model that ran, so a pasted report has provenance', () => {
		const text = reportToText('gemini', reportRow(), LABEL_MAP);

		expect(text.split('\n')[0]).toBe('Gemini · gemini-test-1-002');
	});

	it('falls back to the requested model when the run did not report one', () => {
		const text = reportToText('gemini', reportRow({ model: null }), LABEL_MAP);

		expect(text.split('\n')[0]).toBe('Gemini · gemini-test-1');
	});

	it('renders the verdict, the sections and the verification list like the card does', () => {
		const text = reportToText('gemini', reportRow(), LABEL_MAP);

		expect(text).toContain('Winner: VESQOR (was A)');
		expect(text).toContain('Answer A is more accurate.');
		expect(text).toContain('VESQOR (was A)');
		expect(text).toContain('ChatGPT (was B)');
		expect(text).toContain('Gemini (was C)');
		expect(text).toContain('Strengths:');
		expect(text).toContain('Important omissions:');
		expect(text).toContain('Claims that need verification:');
		expect(text).toContain('- the p99 claim');
	});

	it('copies the judge text verbatim — passage and note keep their newlines and are never re-wrapped', () => {
		const text = reportToText('gemini', reportRow(), LABEL_MAP);

		expect(text).toContain('quoted from A');
		expect(text).toContain('A is tight');
		// The multi-line note from `section()` survives as-is.
		expect(text).toContain('a general\nmulti-line remark');
	});

	it('drops empty finding lists rather than printing empty headings', () => {
		const text = reportToText('gemini', reportRow(), LABEL_MAP);

		expect(text).not.toContain('Useful extras:');
		expect(text).not.toContain('Unnecessary content:');
		expect(text).not.toContain('Specific improvements:');
	});

	it('says so instead of inventing content when the report could not be mapped', () => {
		const text = reportToText(
			'gemini',
			reportRow({ mapped: null, mapping_error: 'label Z is unknown' }),
			LABEL_MAP
		);

		expect(text).toContain(JUDGE_ERROR_COPY.label_not_in_map);
		expect(text).toContain('label Z is unknown');
		expect(text).not.toContain('Winner');
	});

	it('names providers without a label when this judge saw them unlabelled', () => {
		const mapped = mappedReport();
		const text = reportToText('gemini', reportRow({ mapped, label_map: null }), null);

		// No "(was X)" invented: the label was never known.
		expect(text).toContain('Winner: VESQOR');
		expect(text).not.toContain('(was');
	});
});
