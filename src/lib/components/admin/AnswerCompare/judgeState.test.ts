import { describe, expect, it } from 'vitest';
import type {
	JudgeConfig,
	JudgeReports,
	MappedReport,
	RawReport,
	ReportRow
} from '$lib/apis/answer-compare';
import { ERROR_COPY, PROVIDER_IDS } from './state';
import {
	answerSections,
	applyJudgeConfigs,
	capableJudgeIds,
	DEFAULT_CAPABLE,
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
	JUDGE_IDS,
	JUDGE_LABELS,
	judgeButtonDisabledReason,
	judgePhase,
	judgeRole,
	judgesToRun,
	labelsByProvider,
	nameWithLabel,
	startJudging,
	verdictHeadline,
	visibleJudgeIds
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

const config = (overrides: Partial<JudgeConfig> = {}): JudgeConfig => ({
	id: 'gemini',
	configured: true,
	missing: [],
	base_url: 'https://x',
	model: 'gemini-test-1',
	can_judge: true,
	...overrides
});

/** The server's built-in registry (DECISIONS.md#016): the trio cannot judge, sonnet can. */
const defaultRegistry = (): JudgeConfig[] =>
	JUDGE_IDS.map((id) => config({ id, can_judge: DEFAULT_CAPABLE[id], model: `${id}-model` }));

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
			() =>
				applyJudgeResult(
					before,
					'chatgpt',
					reportRow({ judge: 'chatgpt', status: 'pending', report: null, mapped: null })
				)
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
			reportRow({
				id: 'report-2',
				revision: 2,
				status: 'failed',
				report: null,
				mapped: null,
				error: { code: 'malformed_report', message: 'x' }
			})
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
				latest_attempt: reportRow({
					id: 'report-2',
					revision: 2,
					status: 'failed',
					report: null,
					mapped: null,
					error: { code: 'stale', message: 'x' }
				}),
				capable: true,
				outdated: false
			}
		];
		const after = applyJudgeRunState(before, reports);

		expect(after.gemini.report).toBe(before.gemini.report);
		expect(after.gemini.failure).toEqual({ code: 'stale' });
	});

	it('keeps the rendered report when the server reports nothing at all', () => {
		const before = withReport();
		const after = applyJudgeRunState(before, [
			{ judge: 'gemini', current: null, latest_attempt: null, capable: true, outdated: false }
		]);
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
			capable: true,
			outdated
		});
		const flagged = applyJudgeRunState(initialJudgeCards(), [entry(true)]);
		expect(flagged.gemini.outdated).toBe(true);

		const cleared = applyJudgeRunState(flagged, [entry(false)]);
		expect(cleared.gemini.outdated).toBe(false);
	});

	it('is never true without a current report', () => {
		const after = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'gemini', current: null, latest_attempt: null, capable: true, outdated: true }
		]);
		expect(after.gemini.outdated).toBe(false);
	});

	it('is not inherited by a new complete report', () => {
		const flagged = applyJudgeRunState(initialJudgeCards(), [
			{
				judge: 'gemini',
				current: reportRow(),
				latest_attempt: reportRow(),
				capable: true,
				outdated: true
			}
		]);
		expect(flagged.gemini.outdated).toBe(true);

		const rejudged = applyJudgeResult(
			flagged,
			'gemini',
			reportRow({ id: 'report-2', revision: 2 })
		);
		expect(rejudged.gemini.outdated).toBe(false);
		expect(rejudged.gemini.report?.revision).toBe(2);
	});
});

describe('display substitution — not mapping', () => {
	it('inverts label_map into "(was X)" per provider', () => {
		expect(labelsByProvider(LABEL_MAP)).toEqual({ vesqor: 'A', chatgpt: 'B', gemini: 'C' });
		expect(nameWithLabel('chatgpt', LABEL_MAP)).toEqual({
			provider: 'chatgpt',
			name: 'ChatGPT',
			label: 'B'
		});
		expect(nameWithLabel('chatgpt', null).label).toBeNull();
	});

	it('renders a tie with every tied provider and its label', () => {
		const mapped: MappedReport = {
			...mappedReport(),
			verdict: { kind: 'tie', providers: ['chatgpt', 'vesqor'] }
		};
		const headline = verdictHeadline(mapped, LABEL_MAP);
		expect(headline.kind).toBe('tie');
		expect(headline.providers).toEqual([
			{ provider: 'chatgpt', name: 'ChatGPT', label: 'B' },
			{ provider: 'vesqor', name: 'VESQOR', label: 'A' }
		]);
	});

	it("passes the judge's text through untouched, by identity", () => {
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
		expect(described.params).toMatchObject({
			judge: 'VESQOR',
			limitChars: 400000,
			actualChars: 512000
		});
		expect(isJudgeRetryDisabled(after.vesqor)).toBe(true);
	});

	it('not_enough_answers names the available providers', () => {
		const after = applyJudgeNotEnoughAnswers(initialJudgeCards(), 'chatgpt', ['gemini']);
		expect(describeJudgeFailure(after.chatgpt.failure!).params.providers).toBe('Gemini');
	});

	it('button reason: unlock at two answers, then configuration, then running', () => {
		const cards = initialJudgeCards();
		expect(judgeButtonDisabledReason(cards.gemini, 1)?.key).toBe(
			'Judging unlocks once at least two answers exist.'
		);
		expect(judgeButtonDisabledReason(cards.gemini, 2)).toBeNull();

		const unconfigured = applyJudgeConfigs(cards, [
			config({ configured: false, missing: ['ANSWER_COMPARE_GEMINI_MODEL'] })
		]);
		expect(judgeButtonDisabledReason(unconfigured.gemini, 3)).toEqual({
			key: 'Requires configuration: {{missing}}',
			params: { missing: 'ANSWER_COMPARE_GEMINI_MODEL' }
		});

		const running = startJudging(cards, 'gemini', 1);
		expect(judgeButtonDisabledReason(running.gemini, 3)?.key).toBe(
			'This judge is already running.'
		);
	});
});

describe('the judge registry — two registries, not one (DECISIONS.md#016)', () => {
	it('keys one card per judge id, the independent judge included', () => {
		const cards = initialJudgeCards();
		expect(Object.keys(cards)).toEqual(['chatgpt', 'gemini', 'vesqor', 'sonnet']);
		expect(JUDGE_IDS).toEqual([...PROVIDER_IDS, 'sonnet']);
		expect(PROVIDER_IDS as string[]).not.toContain('sonnet');
		expect(JUDGE_LABELS.sonnet).toBe('Sonnet');
	});

	it('before configs arrive, the cards carry the built-in default: sonnet alone judges', () => {
		expect(capableJudgeIds(initialJudgeCards())).toEqual(['sonnet']);
		expect(visibleJudgeIds(initialJudgeCards())).toEqual(['sonnet']);
	});

	it('reads capability from the judges list, in either direction', () => {
		const registry = defaultRegistry();
		expect(capableJudgeIds(applyJudgeConfigs(initialJudgeCards(registry), registry))).toEqual([
			'sonnet'
		]);

		// The per-deployment override brings a participant back and can switch sonnet off.
		const overridden = registry.map((c) =>
			c.id === 'chatgpt'
				? { ...c, can_judge: true }
				: c.id === 'sonnet'
					? { ...c, can_judge: false }
					: c
		);
		expect(capableJudgeIds(applyJudgeConfigs(initialJudgeCards(overridden), overridden))).toEqual([
			'chatgpt'
		]);
		expect(initialJudgeCards(overridden).sonnet.capable).toBe(false);
	});

	it('the run state can flip capability by itself, from the reports entry', () => {
		const entry: JudgeReports = {
			judge: 'chatgpt',
			current: null,
			latest_attempt: null,
			outdated: false,
			capable: true
		};
		const cards = applyJudgeRunState(initialJudgeCards(), [entry]);
		expect(cards.chatgpt.capable).toBe(true);
		expect(applyJudgeRunState(cards, [{ ...entry, capable: false }]).chatgpt.capable).toBe(false);
	});

	it('never marks a judge that cannot judge as "requires configuration"', () => {
		const registry = defaultRegistry().map((c) =>
			c.id === 'vesqor'
				? { ...c, configured: false, missing: ['ANSWER_COMPARE_VESQOR_API_KEY'] }
				: c
		);
		const cards = applyJudgeConfigs(initialJudgeCards(registry), registry);
		expect(cards.vesqor.missing).toBeNull();
		expect(judgePhase(cards.vesqor)).toBe('empty');
		// …while a capable, unconfigured judge is.
		const sonnetMissing = registry.map((c) =>
			c.id === 'sonnet'
				? { ...c, configured: false, missing: ['ANSWER_COMPARE_SONNET_API_KEY'] }
				: c
		);
		expect(
			applyJudgeConfigs(initialJudgeCards(sonnetMissing), sonnetMissing).sonnet.missing
		).toEqual(['ANSWER_COMPARE_SONNET_API_KEY']);
	});

	it('judgesToRun walks the capable set: sonnet alone by default', () => {
		const cards = initialJudgeCards(defaultRegistry());
		expect(judgesToRun(cards)).toEqual(['sonnet']);
		expect(judgesToRun(startJudging(cards, 'sonnet', 1))).toEqual([]);
	});
});

describe('legacy participant judges — visible, read-only', () => {
	const legacyEntry = (): JudgeReports => ({
		judge: 'chatgpt',
		current: reportRow({ judge: 'chatgpt', id: 'legacy-1' }),
		latest_attempt: reportRow({ judge: 'chatgpt', id: 'legacy-1' }),
		outdated: false,
		capable: false
	});

	it('a non-capable judge with a stored report gets a card, but no button', () => {
		const registry = defaultRegistry();
		const cards = applyJudgeRunState(
			applyJudgeConfigs(initialJudgeCards(registry), registry),
			[legacyEntry()],
			registry
		);
		expect(cards.chatgpt.report?.id).toBe('legacy-1');
		expect(cards.chatgpt.capable).toBe(false);
		expect(visibleJudgeIds(cards)).toEqual(['chatgpt', 'sonnet']);
		expect(capableJudgeIds(cards)).toEqual(['sonnet']);
		expect(judgesToRun(cards)).toEqual(['sonnet']);
	});

	it('keeps the legacy report readable even when its old triple is no longer configured', () => {
		const registry = defaultRegistry().map((c) =>
			c.id === 'chatgpt'
				? { ...c, configured: false, missing: ['ANSWER_COMPARE_CHATGPT_API_KEY'] }
				: c
		);
		const cards = applyJudgeRunState(
			applyJudgeConfigs(initialJudgeCards(registry), registry),
			[legacyEntry()],
			registry
		);
		expect(cards.chatgpt.report?.id).toBe('legacy-1');
		expect(cards.chatgpt.missing).toBeNull();
		expect(judgePhase(cards.chatgpt)).toBe('complete');
	});

	it('a non-capable judge with nothing stored gets no card at all', () => {
		const cards = applyJudgeRunState(initialJudgeCards(), [
			{ judge: 'gemini', current: null, latest_attempt: null, outdated: false, capable: false }
		]);
		expect(visibleJudgeIds(cards)).toEqual(['sonnet']);
	});

	it('names the role under the judge: independent, legacy, or nothing', () => {
		const cards = applyJudgeRunState(initialJudgeCards(), [legacyEntry()]);
		expect(judgeRole(cards.sonnet)?.key).toBe('Independent judge');
		expect(judgeRole(cards.chatgpt)?.key).toBe('Participant judge (no longer used)');
		// A participant judge re-enabled by the override has no subtitle.
		const reenabled = applyJudgeRunState(initialJudgeCards(), [
			{ ...legacyEntry(), judge: 'gemini', capable: true }
		]);
		expect(judgeRole(reenabled.gemini)).toBeNull();
	});
});
