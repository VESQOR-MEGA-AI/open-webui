/**
 * Deterministic welcome copy for the sign-in screen.
 *
 * Pure presentation layer: `buildWelcomeMessage` is a total function of
 * (now, firstName, locale) — same input, same output, no clocks read
 * internally, no stores, no fetching. That keeps it unit-testable and makes
 * the rendered greeting stable across re-renders.
 *
 * Nothing here touches authentication: the greeting is text only, and the
 * caller only passes a name it already holds from the existing session flow.
 */

export type TimeOfDay = 'morning' | 'afternoon' | 'evening';

export interface WelcomeInput {
	/** Reference instant (the caller's local `new Date()`). */
	now: Date;
	/** Full name from the existing session/user store, when one is available. */
	firstName?: string | null;
	/** BCP-47 tag used for date formatting; falls back to the runtime locale. */
	locale?: string | null;
}

export interface WelcomeMessage {
	/** i18n key — the English sentence itself, with {{NAME}} / {{DATE}}. */
	key: string;
	/** Interpolation values, only for placeholders the chosen sentence uses. */
	params: Record<string, string>;
	timeOfDay: TimeOfDay | null;
	personalized: boolean;
	usesDate: boolean;
}

export const MORNING_START_HOUR = 5;
export const AFTERNOON_START_HOUR = 12;
export const EVENING_START_HOUR = 17;

/** Longest first name we will greet with; beyond this we keep it generic. */
export const MAX_NAME_LENGTH = 40;

/** Shown when the clock or the user context is unusable. */
export const GENERIC_WELCOME_KEY = 'Welcome back. Sign in to continue.';

interface Template {
	key: string;
	usesDate: boolean;
}

const plain = (key: string): Template => ({ key, usesDate: false });
const dated = (key: string): Template => ({ key, usesDate: true });

const TEMPLATES: Record<TimeOfDay, { named: Template[]; anonymous: Template[] }> = {
	morning: {
		named: [
			plain('Good morning, {{NAME}}. Ready to continue where you left off?'),
			plain('Good morning, {{NAME}}. Let’s get started.'),
			dated('{{DATE}} — good morning, {{NAME}}.')
		],
		anonymous: [
			plain('Good morning. Ready to continue where you left off?'),
			plain('Good morning. Let’s get started.'),
			dated('{{DATE}} — welcome back.')
		]
	},
	afternoon: {
		named: [
			plain('Good afternoon, {{NAME}}. Ready to pick up where you left off?'),
			plain('Good afternoon, {{NAME}}. Let’s get back to it.'),
			dated('{{DATE}} — good afternoon, {{NAME}}.')
		],
		anonymous: [
			plain('Good afternoon. Ready to pick up where you left off?'),
			plain('Good afternoon. Let’s get back to it.'),
			dated('{{DATE}} — welcome back.')
		]
	},
	evening: {
		named: [
			plain('Good evening, {{NAME}}. Ready to continue where you left off?'),
			plain('Good evening, {{NAME}}. Let’s wrap up the day’s work.'),
			dated('{{DATE}} — good evening, {{NAME}}.')
		],
		anonymous: [
			plain('Good evening. Ready to continue where you left off?'),
			plain('Good evening. Let’s wrap up the day’s work.'),
			dated('{{DATE}} — welcome back.')
		]
	}
};

/**
 * Buckets the local hour into a greeting slot.
 * 05:00–11:59 morning, 12:00–16:59 afternoon, 17:00–04:59 evening.
 */
export const resolveTimeOfDay = (now: Date): TimeOfDay => {
	const hour = now.getHours();
	if (hour >= EVENING_START_HOUR || hour < MORNING_START_HOUR) return 'evening';
	if (hour >= AFTERNOON_START_HOUR) return 'afternoon';
	return 'morning';
};

/**
 * First token of a display name, or null when we should not personalize.
 * Rejects blanks and absurdly long values so the heading cannot blow up.
 */
export const extractFirstName = (name?: string | null): string | null => {
	if (typeof name !== 'string') return null;
	const trimmed = name.trim();
	if (!trimmed) return null;
	const first = trimmed.split(/\s+/)[0] ?? '';
	if (!first || first.length > MAX_NAME_LENGTH) return null;
	return first;
};

/** 1-based day of year, computed in UTC so DST shifts cannot skew it. */
export const dayOfYear = (now: Date): number => {
	const startOfYear = Date.UTC(now.getFullYear(), 0, 1);
	const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
	return Math.floor((today - startOfYear) / 86400000) + 1;
};

/** e.g. "Friday, September 26". Returns null if the runtime cannot format it. */
export const formatWelcomeDate = (now: Date, locale?: string | null): string | null => {
	try {
		const formatted = new Intl.DateTimeFormat(locale || undefined, {
			weekday: 'long',
			month: 'long',
			day: 'numeric'
		}).format(now);

		// Keep the month and the day together so a narrow heading never breaks
		// the date across two lines ("Saturday, September / 26").
		return formatted ? formatted.replace(/ (\S+)$/, '\u00A0$1') : null;
	} catch {
		// An unusable locale tag must not break the greeting.
		return null;
	}
};

/**
 * Picks one greeting. The choice is derived from the day of the year, so it is
 * stable for a whole day (no flicker on re-render) yet varies day to day.
 */
export const buildWelcomeMessage = (input: WelcomeInput): WelcomeMessage => {
	const now = input.now;
	const hasUsableDate = now instanceof Date && !Number.isNaN(now.getTime());

	if (!hasUsableDate) {
		return {
			key: GENERIC_WELCOME_KEY,
			params: {},
			timeOfDay: null,
			personalized: false,
			usesDate: false
		};
	}

	const timeOfDay = resolveTimeOfDay(now);
	const firstName = extractFirstName(input.firstName);
	const personalized = firstName !== null;
	const date = formatWelcomeDate(now, input.locale);

	const pool = personalized ? TEMPLATES[timeOfDay].named : TEMPLATES[timeOfDay].anonymous;
	// Without a formattable date we may not use a date-carrying sentence.
	const candidates = date ? pool : pool.filter((template) => !template.usesDate);
	const chosen = candidates.length > 0 ? candidates[dayOfYear(now) % candidates.length] : pool[0];

	const params: Record<string, string> = {};
	if (personalized && firstName && chosen.key.includes('{{NAME}}')) {
		params.NAME = firstName;
	}
	if (chosen.usesDate && date) {
		params.DATE = date;
	}

	return {
		key: chosen.key,
		params,
		timeOfDay,
		personalized,
		usesDate: chosen.usesDate
	};
};
