import type {
	JudgeConfig,
	JudgeId,
	JudgeReports,
	MappedReport,
	ProviderId,
	ReportAnswerSection,
	ReportFinding,
	ReportRow
} from '$lib/apis/answer-compare';
import {
	ERROR_COPY,
	PROVIDER_IDS,
	PROVIDER_LABELS,
	UNKNOWN_ERROR_COPY,
	type CardFailure
} from './state';

/**
 * The judge registry, in the server's fixed order: the compared trio (judges
 * only under a per-deployment override) and the independent judge. Every
 * judge-only loop walks this or a subset of it — never PROVIDER_IDS, which is
 * the answer registry and does not contain the independent judge.
 */
export const JUDGE_IDS: JudgeId[] = [...PROVIDER_IDS, 'sonnet'];

export const JUDGE_LABELS: Record<JudgeId, string> = { ...PROVIDER_LABELS, sonnet: 'Sonnet' };

/**
 * The server's built-in default (DECISIONS.md#016): only the independent judge
 * judges. This is the pre-load state of the cards; the config that arrives from
 * the server corrects it, in either direction, as soon as it is applied.
 */
export const DEFAULT_CAPABLE: Record<JudgeId, boolean> = {
	chatgpt: false,
	gemini: false,
	vesqor: false,
	sonnet: true
};

const ANSWER_PROVIDERS: ReadonlySet<string> = new Set(PROVIDER_IDS);

/** The independent judge is not a compared provider: it writes no answer. */
export const isIndependentJudge = (judge: JudgeId): boolean => !ANSWER_PROVIDERS.has(judge);

/**
 * Judge-card state, with the same discipline as `state.ts`: pure transitions, one
 * private write point, sibling identity preserved.
 *
 * Two rules again shape everything here.
 *
 * 1. **A report, once rendered, is removed by nothing except a successful new
 *    version.** `report` is only ever assigned from a `complete` row; a re-judge
 *    starting or failing leaves it alone, and so does a reload.
 *
 * 2. **This module renders `mapped` and never maps labels itself.** The label →
 *    provider transform lives on the server, in one implementation the tally
 *    also uses. The only label work here is *substitution for display*: inverting
 *    `label_map` to print "(was B)" next to a provider name. The judge's own text
 *    — rationale, note, passage — is passed through untouched.
 */

export const SECTION_FIELDS: { field: keyof Omit<ReportAnswerSection, 'label'>; title: string }[] =
	[
		{ field: 'strengths', title: 'Strengths' },
		{ field: 'errors_or_unsupported', title: 'Errors or unsupported claims' },
		{ field: 'omissions', title: 'Important omissions' },
		{ field: 'useful_extras', title: 'Useful extras' },
		{ field: 'unnecessary', title: 'Unnecessary content' },
		{ field: 'improvements', title: 'Specific improvements' }
	];

/** Judge-specific copy. Anything not here falls back to ERROR_COPY from state.ts. */
export const JUDGE_ERROR_COPY: Record<string, string> = {
	malformed_report:
		"The judge's report could not be read as a valid report. It is not counted. Retry.",
	truncated: "The judge's report was cut off at the output limit. It is not counted. Retry.",
	not_enough_answers: 'Judging needs at least two answers. Available: {{providers}}.',
	oversized:
		'The judging input is {{actualChars}} characters. The configured judging limit for {{judge}} is {{limitChars}}.',
	already_running: 'This judge is already running.',
	not_configured: 'Requires configuration',
	label_not_in_map: 'This report could not be mapped to provider names. The raw report is kept.',
	judge_not_capable: 'This provider cannot act as a judge.'
};

export const JUDGE_RETRY_DISABLED_CODES = [
	'already_running',
	'not_configured',
	'oversized',
	'not_enough_answers',
	'judge_not_capable'
];

export interface JudgeCardState {
	judge: JudgeId;
	/** The last complete report. Replaced only by a newer complete one. */
	report: ReportRow | null;
	inFlight: boolean;
	startedAt: number | null;
	/** Shown as a banner; never removes `report`. */
	failure: CardFailure | null;
	missing: string[] | null;
	/** Server-derived: the current report judged an older answer set. */
	outdated: boolean;
	/**
	 * Whether this judge may be called at all — from `JudgeConfig.can_judge` and
	 * `JudgeReports.capable`. A card that is not capable but holds a report is a
	 * legacy participant judge: shown read-only, never offered "Judge again".
	 */
	capable: boolean;
}

export type JudgeCardsState = Record<JudgeId, JudgeCardState>;

export type JudgePhase =
	| 'requires_configuration'
	| 'judging'
	| 'rejudging'
	| 'failed'
	| 'complete'
	| 'empty';

export const emptyJudgeCard = (
	judge: JudgeId,
	capable: boolean = DEFAULT_CAPABLE[judge]
): JudgeCardState => ({
	judge,
	report: null,
	inFlight: false,
	startedAt: null,
	failure: null,
	missing: null,
	outdated: false,
	capable
});

/**
 * One card per registry entry. Before the judge configs arrive the cards carry
 * the server's built-in default (`DEFAULT_CAPABLE`); `applyJudgeConfigs` /
 * `applyJudgeRunState` correct it as soon as the server has spoken.
 */
export const initialJudgeCards = (configs?: JudgeConfig[]): JudgeCardsState => {
	const capableById = new Map(configs?.map((config) => [config.id, config.can_judge]) ?? []);
	return JUDGE_IDS.reduce((acc, judge) => {
		acc[judge] = emptyJudgeCard(judge, capableById.get(judge) ?? DEFAULT_CAPABLE[judge]);
		return acc;
	}, {} as JudgeCardsState);
};

/** The single write point: replaces one judge's card, keeps the others by identity. */
const withJudgeCard = (
	cards: JudgeCardsState,
	judge: JudgeId,
	next: JudgeCardState
): JudgeCardsState => ({
	...cards,
	[judge]: next
});

export const judgePhase = (card: JudgeCardState): JudgePhase => {
	if (card.missing) return 'requires_configuration';
	if (card.inFlight) return card.report ? 'rejudging' : 'judging';
	if (card.failure) return 'failed';
	if (card.report) return 'complete';
	return 'empty';
};

export const describeJudgeFailure = (
	failure: CardFailure
): { key: string; params: Record<string, string | number> } => {
	const params = { code: failure.code, ...(failure.params ?? {}) };
	const specific = JUDGE_ERROR_COPY[failure.code];
	if (specific) return { key: specific, params };
	const shared = ERROR_COPY[failure.code];
	if (shared) return { key: shared, params };
	return { key: UNKNOWN_ERROR_COPY, params };
};

export const isJudgeRetryDisabled = (card: JudgeCardState): boolean =>
	card.inFlight ||
	(card.failure !== null && JUDGE_RETRY_DISABLED_CODES.includes(card.failure.code));

/** A judging starts. The existing report stays: this is Re-judging, not Judging. */
export const startJudging = (
	cards: JudgeCardsState,
	judge: JudgeId,
	now: number
): JudgeCardsState =>
	withJudgeCard(cards, judge, {
		...cards[judge],
		inFlight: true,
		startedAt: now,
		failure: null,
		missing: null
	});

export const applyJudgeNotConfigured = (
	cards: JudgeCardsState,
	judge: JudgeId,
	missing: string[],
	capable?: boolean
): JudgeCardsState =>
	withJudgeCard(cards, judge, {
		...cards[judge],
		inFlight: false,
		startedAt: null,
		failure: null,
		missing,
		...(capable !== undefined ? { capable } : {})
	});

export const applyJudgeOversized = (
	cards: JudgeCardsState,
	judge: JudgeId,
	limitChars: number,
	actualChars: number
): JudgeCardsState =>
	withJudgeCard(cards, judge, {
		...cards[judge],
		inFlight: false,
		startedAt: null,
		failure: {
			code: 'oversized',
			params: { judge: JUDGE_LABELS[judge], limitChars, actualChars }
		}
	});

export const applyJudgeNotEnoughAnswers = (
	cards: JudgeCardsState,
	judge: JudgeId,
	complete: ProviderId[]
): JudgeCardsState =>
	withJudgeCard(cards, judge, {
		...cards[judge],
		inFlight: false,
		startedAt: null,
		failure: {
			code: 'not_enough_answers',
			params: { providers: complete.map((p) => PROVIDER_LABELS[p]).join(', ') || '—' }
		}
	});

/** A typed failure, from our backend or from the judge via a failed row. The report is untouched. */
export const applyJudgeFailure = (
	cards: JudgeCardsState,
	judge: JudgeId,
	failure: CardFailure
): JudgeCardsState =>
	withJudgeCard(cards, judge, {
		...cards[judge],
		inFlight: false,
		startedAt: null,
		failure
	});

/** Apply one report row, whatever its status. The single entry point for results. */
export const applyJudgeResult = (
	cards: JudgeCardsState,
	judge: JudgeId,
	row: ReportRow
): JudgeCardsState => {
	const card = cards[judge];

	if (row.status === 'complete') {
		return withJudgeCard(cards, judge, {
			...card,
			report: row,
			inFlight: false,
			startedAt: null,
			failure: null,
			missing: null,
			// A fresh report judged the current answers; the banner belongs to the
			// predecessor. The GET that follows every successful POST is what sets
			// `outdated` authoritatively — this only makes sure it is not inherited.
			outdated: false
		});
	}

	if (row.status === 'failed') {
		return withJudgeCard(cards, judge, {
			...card,
			inFlight: false,
			startedAt: null,
			failure: { code: row.error?.code ?? 'upstream_error' }
		});
	}

	return withJudgeCard(cards, judge, {
		...card,
		inFlight: true,
		startedAt: card.startedAt ?? Date.now(),
		failure: null
	});
};

/**
 * Adopt the judge registry. A judge that cannot judge is never "requires
 * configuration": nothing would be called anyway, and a legacy card's report
 * must stay readable whatever its old triple looks like now.
 */
export const applyJudgeConfigs = (
	cards: JudgeCardsState,
	configs: JudgeConfig[]
): JudgeCardsState =>
	configs.reduce((acc, config) => {
		const next =
			config.configured || !config.can_judge
				? acc
				: applyJudgeNotConfigured(acc, config.id, config.missing);
		return withJudgeCard(next, config.id, { ...next[config.id], capable: config.can_judge });
	}, cards);

/**
 * Adopt the stored state: the current report, the latest attempt layered on it,
 * and the server-derived `outdated`. Idempotent; never clears a rendered report
 * the server has no complete row for.
 */
export const applyJudgeRunState = (
	cards: JudgeCardsState,
	reports: JudgeReports[],
	configs: JudgeConfig[] = []
): JudgeCardsState => {
	const configById = new Map(configs.map((config) => [config.id, config]));

	return reports.reduce((acc, entry) => {
		const config = configById.get(entry.judge);
		// The GET says per entry whether the judge may be called; the config is
		// the fallback, and the card keeps what it had when neither speaks.
		const capable = entry.capable ?? config?.can_judge ?? acc[entry.judge].capable;
		if (config && !config.configured && capable) {
			return applyJudgeNotConfigured(acc, entry.judge, config.missing, capable);
		}

		const card = acc[entry.judge];
		const current = entry.current;
		const latest = entry.latest_attempt;
		const report = current ?? card.report;

		let inFlight = false;
		let startedAt: number | null = null;
		let failure: CardFailure | null = null;

		if (latest && latest.status === 'pending') {
			inFlight = true;
			startedAt = card.startedAt ?? Date.now();
		} else if (latest && latest.status === 'failed') {
			if (!current || latest.revision > current.revision) {
				failure = { code: latest.error?.code ?? 'upstream_error' };
			}
		}

		return withJudgeCard(acc, entry.judge, {
			...card,
			report,
			inFlight,
			startedAt,
			failure,
			missing: null,
			outdated: current ? entry.outdated : false,
			capable
		});
	}, cards);
};

/** The line under a judge's name that says what kind of judge it is, or null for a plain participant judge. */
export const judgeRole = (
	card: JudgeCardState
): { key: string; params: Record<string, never> } | null => {
	if (!card.capable) return { key: 'Participant judge (no longer used)', params: {} };
	if (isIndependentJudge(card.judge)) return { key: 'Independent judge', params: {} };
	return null;
};

export const judgeElapsedSeconds = (card: JudgeCardState, now: number): number =>
	card.startedAt === null ? 0 : Math.max(0, Math.floor((now - card.startedAt) / 1000));

/** Why a judge button is disabled, as an i18n key with params — or null when enabled. */
export const judgeButtonDisabledReason = (
	card: JudgeCardState,
	completeAnswers: number
): { key: string; params: Record<string, string | number> } | null => {
	if (completeAnswers < 2)
		return { key: 'Judging unlocks once at least two answers exist.', params: {} };
	if (card.missing)
		return {
			key: 'Requires configuration: {{missing}}',
			params: { missing: card.missing.join(', ') }
		};
	if (card.inFlight) return { key: 'This judge is already running.', params: {} };
	return null;
};

// ---------------------------------------------------------------------------
// Display substitution — not mapping
// ---------------------------------------------------------------------------

export interface NamedProvider {
	provider: ProviderId;
	/** The provider's display name. */
	name: string;
	/** The anonymous label this provider had for this judge, or null when unknown. */
	label: string | null;
}

/** Invert label_map: provider -> the label it had. "(was B)" comes from here. */
export const labelsByProvider = (
	labelMap: Record<string, ProviderId> | null
): Partial<Record<ProviderId, string>> => {
	const out: Partial<Record<ProviderId, string>> = {};
	for (const [label, provider] of Object.entries(labelMap ?? {})) {
		out[provider] = label;
	}
	return out;
};

export const nameWithLabel = (
	provider: ProviderId,
	labelMap: Record<string, ProviderId> | null
): NamedProvider => ({
	provider,
	name: PROVIDER_LABELS[provider],
	label: labelsByProvider(labelMap)[provider] ?? null
});

/** The verdict line: kind plus the named providers, each with its label. */
export const verdictHeadline = (
	mapped: MappedReport,
	labelMap: Record<string, ProviderId> | null
): { kind: MappedReport['verdict']['kind']; providers: NamedProvider[] } => ({
	kind: mapped.verdict.kind,
	providers: mapped.verdict.providers.map((provider) => nameWithLabel(provider, labelMap))
});

export interface RenderedList {
	field: keyof Omit<ReportAnswerSection, 'label'>;
	title: string;
	items: ReportFinding[];
}

export interface RenderedSection {
	named: NamedProvider;
	/** Only the non-empty lists, in the fixed order. */
	lists: RenderedList[];
}

/** One section per provider in the fixed order, with empty lists dropped. Findings are the judge's own objects. */
export const answerSections = (
	mapped: MappedReport,
	labelMap: Record<string, ProviderId> | null
): RenderedSection[] =>
	PROVIDER_IDS.filter((provider) => mapped.answers[provider] !== undefined).map((provider) => {
		const section = mapped.answers[provider] as ReportAnswerSection;
		return {
			named: nameWithLabel(provider, labelMap),
			lists: SECTION_FIELDS.map(({ field, title }) => ({
				field,
				title,
				items: section[field]
			})).filter((list) => list.items.length > 0)
		};
	});

/** The judge-capable judges, in `JUDGE_IDS` order — every judge BUTTON loop walks this, never `PROVIDER_IDS`. */
export const capableJudgeIds = (cards: JudgeCardsState): JudgeId[] =>
	JUDGE_IDS.filter((judge) => cards[judge].capable);

/**
 * The judges that get a CARD: the capable ones, plus any legacy judge whose
 * stored report is still on the run (read-only, never re-judged). A judge that
 * can neither be called nor show anything gets no card.
 */
export const visibleJudgeIds = (cards: JudgeCardsState): JudgeId[] =>
	JUDGE_IDS.filter((judge) => cards[judge].capable || cards[judge].report !== null);

/**
 * The judges "Run all judges" will actually call: capable, configured, not
 * already running, and not sitting on a state the server would skip anyway.
 * Same reason as `providersToGenerate` — the dialog quotes this count.
 */
export const judgesToRun = (cards: JudgeCardsState): JudgeId[] =>
	capableJudgeIds(cards).filter((judge) => {
		const card = cards[judge];
		if (card.missing !== null || card.inFlight) return false;
		return !(card.failure !== null && JUDGE_RETRY_DISABLED_CODES.includes(card.failure.code));
	});
