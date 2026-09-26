import { describe, expect, it } from 'vitest';
import {
	AFTERNOON_START_HOUR,
	buildWelcomeMessage,
	dayOfYear,
	EVENING_START_HOUR,
	extractFirstName,
	formatWelcomeDate,
	GENERIC_WELCOME_KEY,
	MAX_NAME_LENGTH,
	MORNING_START_HOUR,
	resolveTimeOfDay,
	type TimeOfDay
} from './welcome-message';

/** Local-time constructor keeps the hour buckets timezone-independent. */
const at = (year: number, month: number, day: number, hour: number, minute = 0) =>
	new Date(year, month - 1, day, hour, minute, 0, 0);

const PLACEHOLDER = /\{\{[A-Z_]+\}\}/g;

describe('resolveTimeOfDay', () => {
	it('buckets morning hours', () => {
		for (const hour of [MORNING_START_HOUR, 8, 11]) {
			expect(resolveTimeOfDay(at(2026, 9, 26, hour))).toBe('morning');
		}
	});

	it('buckets afternoon hours', () => {
		for (const hour of [AFTERNOON_START_HOUR, 14, EVENING_START_HOUR - 1]) {
			expect(resolveTimeOfDay(at(2026, 9, 26, hour))).toBe('afternoon');
		}
	});

	it('buckets evening and the small hours together', () => {
		for (const hour of [EVENING_START_HOUR, 20, 23, 0, MORNING_START_HOUR - 1]) {
			expect(resolveTimeOfDay(at(2026, 9, 26, hour))).toBe('evening');
		}
	});
});

describe('extractFirstName', () => {
	it('takes the first token of a display name', () => {
		expect(extractFirstName('Alex Smith')).toBe('Alex');
		expect(extractFirstName('  Alex   Smith  ')).toBe('Alex');
		expect(extractFirstName('Alex')).toBe('Alex');
	});

	it('returns null when there is nothing safe to personalize with', () => {
		expect(extractFirstName(undefined)).toBeNull();
		expect(extractFirstName(null)).toBeNull();
		expect(extractFirstName('')).toBeNull();
		expect(extractFirstName('   ')).toBeNull();
		expect(extractFirstName(12 as unknown as string)).toBeNull();
	});

	it('refuses absurdly long values so the heading cannot blow up', () => {
		expect(extractFirstName('A'.repeat(MAX_NAME_LENGTH))).toBe('A'.repeat(MAX_NAME_LENGTH));
		expect(extractFirstName('A'.repeat(MAX_NAME_LENGTH + 1))).toBeNull();
		expect(extractFirstName(`${'A'.repeat(MAX_NAME_LENGTH + 1)} Smith`)).toBeNull();
	});
});

describe('formatWelcomeDate', () => {
	it('formats a weekday and date for the requested locale', () => {
		expect(formatWelcomeDate(at(2026, 9, 26, 9), 'en-US')).toBe('Saturday, September\u00A026');
	});

	it('keeps month and day on one line with a non-breaking space', () => {
		const formatted = formatWelcomeDate(at(2026, 9, 26, 9), 'en-US') as string;

		// A plain space here would let the heading break the date in half.
		expect(formatted).toContain('\u00A0');
		expect(formatted.split('\u00A0')).toHaveLength(2);
		expect(formatted).not.toMatch(/September 26/);
	});

	it('returns null instead of throwing on an unusable locale', () => {
		expect(formatWelcomeDate(at(2026, 9, 26, 9), 'not a locale!!')).toBeNull();
	});
});

describe('dayOfYear', () => {
	it('is 1-based and stable', () => {
		expect(dayOfYear(at(2026, 1, 1, 0))).toBe(1);
		expect(dayOfYear(at(2026, 12, 31, 23))).toBe(365);
		expect(dayOfYear(at(2026, 9, 26, 9))).toBe(269);
	});

	it('does not drift across a DST boundary', () => {
		expect(dayOfYear(at(2026, 3, 8, 1)) + 1).toBe(dayOfYear(at(2026, 3, 9, 1)));
	});
});

describe('buildWelcomeMessage', () => {
	it('greets a returning user by first name', () => {
		const welcome = buildWelcomeMessage({
			now: at(2026, 9, 26, 9),
			firstName: 'Alex Smith',
			locale: 'en-US'
		});

		expect(welcome.personalized).toBe(true);
		expect(welcome.timeOfDay).toBe<TimeOfDay>('morning');
		expect(welcome.params.NAME).toBe('Alex');
		expect(welcome.key).toContain('{{NAME}}');
	});

	it('falls back to an anonymous greeting when no name is available', () => {
		for (const firstName of [undefined, null, '', '   ']) {
			const welcome = buildWelcomeMessage({ now: at(2026, 9, 26, 9), firstName, locale: 'en-US' });

			expect(welcome.personalized).toBe(false);
			expect(welcome.key).not.toContain('{{NAME}}');
			expect(welcome.params.NAME).toBeUndefined();
			expect(welcome.key.startsWith('Sign in to')).toBe(false);
		}
	});

	it('never leaks a name into the anonymous greeting', () => {
		const anonymous = buildWelcomeMessage({ now: at(2026, 9, 26, 9), locale: 'en-US' });

		expect(Object.values(anonymous.params)).not.toContain('Alex');
	});

	it('uses the right greeting slot for each part of the day', () => {
		const hours: Array<[number, string]> = [
			[9, 'Good morning'],
			[14, 'Good afternoon'],
			[20, 'Good evening']
		];

		// dayOfYear(2026-09-24) === 267, and 267 % 3 === 0 -> a greeting that
		// opens with the slot word (the dated template leads with the date).
		for (const [hour, prefix] of hours) {
			const welcome = buildWelcomeMessage({ now: at(2026, 9, 24, hour), locale: 'en-US' });
			expect(welcome.key.startsWith(prefix)).toBe(true);
		}
	});

	it('still carries the slot inside a dated greeting', () => {
		// dayOfYear(2026-09-26) === 269, and 269 % 3 === 2 -> the dated template.
		// The anonymous dated greeting is deliberately slot-free ("— welcome back.")
		// so the slot only appears on the personalized dated variant.
		const hours: Array<[number, string]> = [
			[9, 'good morning'],
			[14, 'good afternoon'],
			[20, 'good evening']
		];

		for (const [hour, phrase] of hours) {
			const welcome = buildWelcomeMessage({
				now: at(2026, 9, 26, hour),
				firstName: 'Alex',
				locale: 'en-US'
			});

			expect(welcome.key.startsWith('{{DATE}} — ')).toBe(true);
			expect(welcome.key).toContain(phrase);
			expect(welcome.params.DATE).toBe('Saturday, September\u00A026');
			expect(welcome.params.NAME).toBe('Alex');
		}
	});

	it('uses a slot-free dated greeting for anonymous visitors', () => {
		const welcome = buildWelcomeMessage({ now: at(2026, 9, 26, 9), locale: 'en-US' });

		expect(welcome.usesDate).toBe(true);
		expect(welcome.key.startsWith('{{DATE}} — ')).toBe(true);
		expect(welcome.key).not.toContain('{{NAME}}');
	});

	it('selects deterministically — same input, same message', () => {
		const input = { now: at(2026, 9, 26, 9), firstName: 'Alex Smith', locale: 'en-US' };

		expect(buildWelcomeMessage(input)).toEqual(buildWelcomeMessage(input));
	});

	it('keeps the same wording for a whole day but varies across days', () => {
		const monday = buildWelcomeMessage({ now: at(2026, 9, 25, 9), locale: 'en-US' });
		const sameMonday = buildWelcomeMessage({ now: at(2026, 9, 25, 10), locale: 'en-US' });
		const saturday = buildWelcomeMessage({ now: at(2026, 9, 26, 9), locale: 'en-US' });

		expect(sameMonday.key).toBe(monday.key);
		expect(saturday.key).not.toBe(monday.key);
	});

	it('incorporates the formatted date when the selector picks a dated greeting', () => {
		// dayOfYear(2026-09-26) === 269, and 269 % 3 === 2 -> the dated template.
		const welcome = buildWelcomeMessage({ now: at(2026, 9, 26, 9), locale: 'en-US' });

		expect(welcome.usesDate).toBe(true);
		expect(welcome.params.DATE).toBe('Saturday, September\u00A026');
		expect(welcome.key).toContain('{{DATE}}');
	});

	it('avoids dated greetings when the date cannot be formatted', () => {
		const welcome = buildWelcomeMessage({
			now: at(2026, 9, 26, 9),
			firstName: 'Alex Smith',
			locale: 'not a locale!!'
		});

		expect(welcome.usesDate).toBe(false);
		expect(welcome.key).not.toContain('{{DATE}}');
		expect(welcome.key).toContain('{{NAME}}');
		expect(welcome.params.DATE).toBeUndefined();
	});

	it('returns a generic fallback when the clock is unusable', () => {
		for (const now of [new Date('nope'), new Date(NaN), undefined as unknown as Date]) {
			const welcome = buildWelcomeMessage({ now, firstName: 'Alex Smith', locale: 'en-US' });

			expect(welcome.key).toBe(GENERIC_WELCOME_KEY);
			expect(welcome.timeOfDay).toBeNull();
			expect(welcome.personalized).toBe(false);
			expect(welcome.params).toEqual({});
		}
	});

	it('never returns an uninterpolatable sentence', () => {
		for (let hour = 0; hour < 24; hour++) {
			for (const firstName of [null, 'Alex Smith']) {
				const welcome = buildWelcomeMessage({
					now: at(2026, 9, 26, hour),
					firstName,
					locale: 'en-US'
				});
				const placeholders = welcome.key.match(PLACEHOLDER) ?? [];

				for (const placeholder of placeholders) {
					const name = placeholder.slice(2, -2);
					expect(welcome.params[name]).toBeTruthy();
				}
			}
		}
	});

	it('keeps copy short enough for the heading on mobile', () => {
		for (let hour = 0; hour < 24; hour++) {
			const welcome = buildWelcomeMessage({
				now: at(2026, 9, 26, hour),
				firstName: 'Alex',
				locale: 'en-US'
			});

			expect(welcome.key.length).toBeLessThanOrEqual(72);
			expect(welcome.key).not.toContain('\n');

			// No emoji: every code point stays below the emoji blocks (U+1F000+),
			// which still allows the em dash and the typographic apostrophe.
			for (const char of welcome.key) {
				expect(char.codePointAt(0) ?? 0).toBeLessThan(0x1f000);
			}
		}
	});
});
