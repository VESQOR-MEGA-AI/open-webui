import type { AnswerRow, ProviderAnswers, ProviderConfig, ProviderId } from '$lib/apis/answer-compare';

/**
 * Card state for the answer-comparison page, as a plain module.
 *
 * Two rules shape everything here.
 *
 * 1. **An answer, once rendered, is removed by nothing except a successful new
 *    version.** Not by a retry starting, not by a retry failing, not by a
 *    reload. So `answer` is only ever assigned from a `complete` row, and every
 *    other transition leaves it alone. "Regenerating" is therefore a distinct
 *    phase from "Generating": one has an answer underneath it and one does not.
 *
 * 2. **The three cards are independent.** Every transition returns a new map in
 *    which only the touched provider's object is new — the other two are the
 *    *same object references*, which is what the tests assert with `toBe`. A
 *    transition that rebuilt all three would pass a deep-equality test and still
 *    be wrong the moment someone added a field.
 */

export const PROVIDER_IDS: ProviderId[] = ['chatgpt', 'gemini', 'vesqor'];

/** Display names. Shown deliberately: this is an admin benchmark of named systems. */
export const PROVIDER_LABELS: Record<ProviderId, string> = {
	chatgpt: 'ChatGPT',
	gemini: 'Gemini',
	vesqor: 'VESQOR'
};

/**
 * One message per backend error code. The keys are the i18n keys, which in this
 * project are also the English display text.
 *
 * Every code the backend can emit must appear here; a code with no entry falls
 * through to UNKNOWN_ERROR_COPY, which carries the code rather than hiding it.
 */
export const ERROR_COPY: Record<string, string> = {
	auth: 'The provider rejected the credentials.',
	rate_limit: 'The provider is rate limiting. Try again shortly.',
	timeout: 'The provider did not respond in time.',
	context_length_exceeded: "The input is longer than the provider's context window.",
	upstream_error: 'The provider returned an error.',
	network: 'Could not reach the provider.',
	malformed_response: 'The provider returned a response this page could not read.',
	stale: 'The previous attempt was interrupted. Retry to try again.',
	not_configured: 'Requires configuration',
	oversized:
		'This input is {{actualChars}} characters. The configured limit for {{provider}} is {{limitChars}}.',
	already_running: 'Generation already in progress.',
	unauthorized: 'Your session expired or admin access is required.',
	answer_vanished: 'The provider failed: {{code}}.',
	run_not_found: 'This run is no longer available.',
	unknown_provider: 'The provider failed: {{code}}.',
	prompt_required: 'Enter a prompt first.',
	judge_not_capable: 'This provider cannot act as a judge.'
};

export const UNKNOWN_ERROR_COPY = 'The provider failed: {{code}}.';

/** Codes whose Retry control is disabled, because retrying cannot help yet. */
export const RETRY_DISABLED_CODES = ['already_running', 'not_configured', 'oversized'];

export interface CardFailure {
	code: string;
	/** Interpolation values for the copy, e.g. the two numbers of an oversized input. */
	params?: Record<string, string | number>;
}

export interface CardState {
	provider: ProviderId;
	/** The last successfully rendered answer. Replaced only by a newer complete one. */
	answer: AnswerRow | null;
	/** A generation is in flight for this provider. */
	inFlight: boolean;
	/** Epoch milliseconds the in-flight generation started, for the elapsed counter. */
	startedAt: number | null;
	/** Shown as a banner; never removes `answer`. */
	failure: CardFailure | null;
	/** Set when the provider is not configured: the exact variable names still needed. */
	missing: string[] | null;
}

export type CardsState = Record<ProviderId, CardState>;

export type CardPhase =
	| 'requires_configuration'
	| 'generating'
	| 'regenerating'
	| 'failed'
	| 'complete'
	| 'empty';

export const emptyCard = (provider: ProviderId): CardState => ({
	provider,
	answer: null,
	inFlight: false,
	startedAt: null,
	failure: null,
	missing: null
});

export const initialCards = (): CardsState =>
	PROVIDER_IDS.reduce((acc, provider) => {
		acc[provider] = emptyCard(provider);
		return acc;
	}, {} as CardsState);

/**
 * Replace exactly one provider's card, keeping the other two object identities.
 * Every transition in this module goes through here — that is the mechanism by
 * which one card can never disturb another.
 */
const withCard = (cards: CardsState, provider: ProviderId, next: CardState): CardsState => ({
	...cards,
	[provider]: next
});

export const cardPhase = (card: CardState): CardPhase => {
	if (card.missing) return 'requires_configuration';
	if (card.inFlight) return card.answer ? 'regenerating' : 'generating';
	if (card.failure) return 'failed';
	if (card.answer) return 'complete';
	return 'empty';
};

/** The copy for a failure, as an i18n key plus its interpolation values. */
export const describeFailure = (
	failure: CardFailure
): { key: string; params: Record<string, string | number> } => {
	const known = ERROR_COPY[failure.code];
	if (known) {
		return { key: known, params: { code: failure.code, ...(failure.params ?? {}) } };
	}
	return { key: UNKNOWN_ERROR_COPY, params: { code: failure.code, ...(failure.params ?? {}) } };
};

export const isRetryDisabled = (card: CardState): boolean =>
	card.inFlight || (card.failure !== null && RETRY_DISABLED_CODES.includes(card.failure.code));

/**
 * A generation starts. The existing answer stays exactly where it is — this is
 * the difference between "Regenerating" and "Generating", and the reason a
 * spinner never covers a rendered answer.
 */
export const startGeneration = (
	cards: CardsState,
	provider: ProviderId,
	now: number
): CardsState =>
	withCard(cards, provider, {
		...cards[provider],
		inFlight: true,
		startedAt: now,
		// The previous attempt's banner goes; the previous answer does not.
		failure: null,
		missing: null
	});

/** A provider reported unconfigured: name the variables, never fake an answer. */
export const applyNotConfigured = (
	cards: CardsState,
	provider: ProviderId,
	missing: string[]
): CardsState =>
	withCard(cards, provider, {
		...cards[provider],
		inFlight: false,
		startedAt: null,
		failure: null,
		missing
	});

export const applyOversized = (
	cards: CardsState,
	provider: ProviderId,
	limitChars: number,
	actualChars: number
): CardsState =>
	withCard(cards, provider, {
		...cards[provider],
		inFlight: false,
		startedAt: null,
		failure: {
			code: 'oversized',
			params: {
				provider: PROVIDER_LABELS[provider],
				limitChars,
				actualChars
			}
		}
	});

/** A typed failure from our backend, or from a provider via a failed row. */
export const applyFailure = (
	cards: CardsState,
	provider: ProviderId,
	failure: CardFailure
): CardsState =>
	withCard(cards, provider, {
		...cards[provider],
		inFlight: false,
		startedAt: null,
		// answer is untouched on purpose: losing a good answer to a failed retry
		// is the exact bug the backend was fixed to prevent.
		failure
	});

/** Apply one answer row, whatever its status. The single entry point for results. */
export const applyResult = (cards: CardsState, provider: ProviderId, row: AnswerRow): CardsState => {
	const card = cards[provider];

	if (row.status === 'complete') {
		return withCard(cards, provider, {
			...card,
			answer: row,
			inFlight: false,
			startedAt: null,
			failure: null,
			missing: null
		});
	}

	if (row.status === 'failed') {
		return withCard(cards, provider, {
			...card,
			inFlight: false,
			startedAt: null,
			failure: { code: row.error?.code ?? 'upstream_error' }
		});
	}

	// pending: the row exists, the call has not settled. Stay in flight.
	return withCard(cards, provider, {
		...card,
		inFlight: true,
		startedAt: card.startedAt ?? Date.now(),
		failure: null
	});
};

/** Mark the providers a run reported as unconfigured, leaving the others alone. */
export const applyProviderConfigs = (
	cards: CardsState,
	configs: ProviderConfig[]
): CardsState =>
	configs.reduce(
		(acc, config) =>
			config.configured
				? acc
				: applyNotConfigured(acc, config.id, config.missing),
		cards
	);

/**
 * Adopt the stored state of a run: the current answer, with the latest attempt
 * layered on top of it. Used on reload and after any connection-level failure —
 * the cases where the page must ask rather than guess.
 */
export const applyRunState = (
	cards: CardsState,
	answers: ProviderAnswers[],
	configs: ProviderConfig[] = []
): CardsState => {
	const configById = new Map(configs.map((config) => [config.id, config]));

	return answers.reduce((acc, entry) => {
		const config = configById.get(entry.provider);
		if (config && !config.configured) {
			return applyNotConfigured(acc, entry.provider, config.missing);
		}

		const card = acc[entry.provider];
		const current = entry.current;
		const latest = entry.latest_attempt;

		// The stored complete answer wins over anything rendered locally, but a
		// run that has no complete answer never clears one we already have.
		const answer = current ?? card.answer;

		let inFlight = false;
		let startedAt: number | null = null;
		let failure: CardFailure | null = null;

		if (latest && latest.status === 'pending') {
			inFlight = true;
			startedAt = card.startedAt ?? Date.now();
		} else if (latest && latest.status === 'failed') {
			// Only a failure *newer* than the rendered answer is worth a banner.
			if (!current || latest.revision > current.revision) {
				failure = { code: latest.error?.code ?? 'upstream_error' };
			}
		}

		return withCard(acc, entry.provider, {
			...card,
			answer,
			inFlight,
			startedAt,
			failure,
			missing: null
		});
	}, cards);
};

/** Seconds since a generation started, for the counter next to the spinner. */
export const elapsedSeconds = (card: CardState, now: number): number =>
	card.startedAt === null ? 0 : Math.max(0, Math.floor((now - card.startedAt) / 1000));

/** After a minute, say why it is taking so long instead of holding a bare spinner. */
export const SLOW_GENERATION_AFTER_SECONDS = 60;

/** Re-read delays after a connection-level failure: once, then a couple more. */
export const RECOVERY_DELAYS_MS = [2000, 6000, 15000];

/**
 * How many provider requests "Generate all three answers" will actually send.
 *
 * The cost dialog states this number, so it must be what really goes out: an
 * unconfigured provider is never called, and a warning that misstates the cost
 * is worse than no warning.
 */
export const providersToGenerate = (providers: ProviderConfig[]): ProviderId[] =>
	PROVIDER_IDS.filter((id) => providers.some((config) => config.id === id && config.configured));
