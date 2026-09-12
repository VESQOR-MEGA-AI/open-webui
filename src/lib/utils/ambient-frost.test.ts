import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	FROST_CLASS,
	FROST_INSTANT_CLASS,
	SETTLE_MS,
	activateAmbientFrost,
	initAmbientFrost,
	isAmbientFrostActive,
	isAmbientFrostArmed,
	isSettled,
	resetAmbientFrost,
	teardownAmbientFrost
} from './ambient-frost';

/**
 * Minimal DOM double — enough for the controller's API surface (document,
 * documentElement, body, add/removeEventListener, matchMedia, dispatch).
 * Avoids pulling jsdom into the project's dependency set for one module.
 */
const makeClassList = () => {
	const set = new Set<string>();
	return {
		add: (c: string) => set.add(c),
		remove: (c: string) => set.delete(c),
		toggle: (c: string, on?: boolean) => {
			if (on === undefined) {
				if (set.has(c)) {
					set.delete(c);
				} else {
					set.add(c);
				}
				return;
			}
			if (on) {
				set.add(c);
			} else {
				set.delete(c);
			}
		},
		contains: (c: string) => set.has(c),
		_values: () => Array.from(set)
	};
};

const makeEnv = (reducedMotion = false) => {
	const listeners = new Map<string, Set<EventListener>>();
	const html = { classList: makeClassList() };
	const body = { classList: makeClassList() };
	const doc = {
		documentElement: html,
		body,
		addEventListener: (type: string, handler: EventListener) => {
			if (!listeners.has(type)) listeners.set(type, new Set());
			listeners.get(type)!.add(handler);
		},
		removeEventListener: (type: string, handler: EventListener) => {
			listeners.get(type)?.delete(handler);
		}
	};
	const win = {
		matchMedia: vi.fn(() => ({ matches: reducedMotion }))
	};

	// Controllable clock: the controller ignores trigger events during the
	// post-load settle window, so tests advance time to simulate a real user
	// acting after the page has settled.
	let clock = 1_000_000;

	return {
		doc,
		win,
		html,
		body,
		now: () => clock,
		/** Advance past the settle window so events count as real interactions. */
		settle: () => {
			clock += 5_000;
		},
		/** fire every listener registered for a type */
		fire: (type: string) => {
			for (const handler of Array.from(listeners.get(type) ?? new Set<EventListener>())) {
				handler(new Event(type));
			}
		},
		listenerCount: (type: string) => listeners.get(type)?.size ?? 0,
		allListenerCount: () =>
			Array.from(listeners.values()).reduce((sum, s) => sum + s.size, 0)
	};
};

const asEnv = (e: ReturnType<typeof makeEnv>) =>
	({ doc: e.doc, win: e.win, now: e.now }) as unknown as Parameters<typeof initAmbientFrost>[0];

beforeEach(() => {
	teardownAmbientFrost();
});

afterEach(() => {
	teardownAmbientFrost();
});

describe('ambient-frost controller', () => {
	it('starts armed and NOT frosted (clear glass on load)', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));

		expect(isAmbientFrostArmed()).toBe(true);
		expect(isAmbientFrostActive()).toBe(false);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(false);
		expect(e.body.classList.contains(FROST_CLASS)).toBe(false);
	});

	it('frosts on the first interaction and pauses the matrix', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		e.settle();

		e.fire('pointerdown');

		expect(isAmbientFrostActive()).toBe(true);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(true);
		expect(e.body.classList.contains(FROST_CLASS)).toBe(true);
	});

	it.each([
		'pointerdown',
		'mousedown',
		'touchstart',
		'keydown',
		'wheel',
		'scroll',
		'click',
		'focusin'
	])('triggers on %s', (type) => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		e.settle();

		e.fire(type);

		expect(isAmbientFrostActive()).toBe(true);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(true);
	});

	it('ignores events during the post-load settle window (load stays clear)', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));

		// Restored scroll position / autofocus on load — not a user action.
		e.fire('scroll');
		e.fire('focusin');
		e.fire('pointerdown');

		expect(isSettled()).toBe(false);
		expect(isAmbientFrostActive()).toBe(false);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(false);
	});

	it('becomes triggerable after the settle window elapses', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));

		e.settle();

		expect(isSettled()).toBe(true);
		e.fire('click');
		expect(isAmbientFrostActive()).toBe(true);
	});

	it('exposes the settle window as a positive duration', () => {
		expect(SETTLE_MS).toBeGreaterThan(0);
		expect(SETTLE_MS).toBeLessThanOrEqual(1000);
	});

	it('is idempotent — a burst of events activates exactly once', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		e.settle();

		const first = activateAmbientFrost();
		const second = activateAmbientFrost();
		const third = activateAmbientFrost();

		expect(first).toBe(true);
		expect(second).toBe(false);
		expect(third).toBe(false);
		expect(isAmbientFrostActive()).toBe(true);
	});

	it('detaches its listeners after activating (no repeated transitions)', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		expect(e.allListenerCount()).toBeGreaterThan(0);

		e.settle();
		e.fire('pointerdown');

		expect(e.allListenerCount()).toBe(0);
	});

	it('reset returns to clear glass and re-arms the trigger', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		e.settle();
		e.fire('pointerdown');
		expect(isAmbientFrostActive()).toBe(true);

		resetAmbientFrost();

		expect(isAmbientFrostActive()).toBe(false);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(false);
		expect(e.body.classList.contains(FROST_CLASS)).toBe(false);

		// re-armed: a new interaction works again (refresh / reopen report)
		e.settle();
		e.fire('click');
		expect(isAmbientFrostActive()).toBe(true);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(true);
	});

	it('reduced motion still reaches the final dark state, with the instant flag', () => {
		const e = makeEnv(true);
		initAmbientFrost(asEnv(e));
		e.settle();

		e.fire('pointerdown');

		expect(e.html.classList.contains(FROST_CLASS)).toBe(true);
		expect(e.html.classList.contains(FROST_INSTANT_CLASS)).toBe(true);
	});

	it('does not set the instant flag without reduced motion', () => {
		const e = makeEnv(false);
		initAmbientFrost(asEnv(e));
		e.settle();

		e.fire('pointerdown');

		expect(e.html.classList.contains(FROST_CLASS)).toBe(true);
		expect(e.html.classList.contains(FROST_INSTANT_CLASS)).toBe(false);
	});

	it('init is safe to call twice — no stacked listeners', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		const afterFirst = e.allListenerCount();
		initAmbientFrost(asEnv(e));

		expect(e.allListenerCount()).toBe(afterFirst);
	});

	it('is a no-op without a DOM (SSR)', () => {
		const cleanup = initAmbientFrost({ doc: undefined, win: undefined });

		expect(isAmbientFrostArmed()).toBe(false);
		expect(activateAmbientFrost()).toBe(false);
		expect(typeof cleanup).toBe('function');
		expect(() => cleanup()).not.toThrow();
	});

	it('teardown removes listeners and classes', () => {
		const e = makeEnv();
		initAmbientFrost(asEnv(e));
		e.settle();
		e.fire('pointerdown');

		teardownAmbientFrost();

		expect(isAmbientFrostArmed()).toBe(false);
		expect(isAmbientFrostActive()).toBe(false);
		expect(e.html.classList.contains(FROST_CLASS)).toBe(false);
	});
});
