/**
 * VESQOR ambient frost — the single controller for the living-Matrix
 * background's interaction state on chat.vesqorai.com.
 *
 * Behaviour (owner spec 2026-09-12):
 *   - On load / refresh the matrix is in its normal CLEAR-GLASS state and
 *     animating.
 *   - The FIRST meaningful user interaction (click, tap, touch, scroll,
 *     wheel, key, focus, or an explicitly reported event such as opening an
 *     existing report or refreshing data) IMMEDIATELY pauses the matrix
 *     animation and begins a gradual transition of the background layer into
 *     a dark, opaque, sandblasted/frosted glass.
 *   - The transition is IDEMPOTENT: once activated, further interactions do
 *     nothing — no restart, no re-run, no flicker.
 *   - The animation is PAUSED (`animation-play-state: paused` via the
 *     `ambient-frosted` class), never destroyed/recreated — so the matrix
 *     keeps its position instead of snapping back to frame zero.
 *   - `resetAmbientFrost()` returns the page to the clear-glass state and
 *     re-arms the trigger — used on initial load and on SPA navigation so
 *     reopening a report (or refreshing) replays the experience.
 *   - `prefers-reduced-motion: reduce` removes the visual transition while
 *     still applying the final dark background state.
 *
 * All DOM access is injectable so the controller is unit-testable without a
 * browser. This module is the ONLY place that knows the frost classes; pages
 * and components call into it instead of toggling classes themselves.
 */

/** Class on `<html>`; CSS keys every frost rule off this single hook. */
export const FROST_CLASS = 'ambient-frosted';
/** Added alongside FROST_CLASS when the user prefers reduced motion. */
export const FROST_INSTANT_CLASS = 'ambient-frosted-instant';

/**
 * Guard window after a PAGE LOAD during which trigger events are ignored.
 *
 * Browsers fire synthetic load-time events that are not user interactions:
 * restoring the scroll position (`scroll`), scroll-linked effects from
 * restored `:target` anchors, and auto-focused inputs (`focusin`). Without a
 * guard the page would frost during load, breaking the "on initial load the
 * background is in its normal clear-glass state" acceptance criterion. A real
 * user cannot reach and manipulate the UI within this window.
 *
 * The guard applies ONLY to the initial page load. SPA navigation
 * (`resetAmbientFrost`) re-arms with no guard, because the user is already
 * mid-interaction and must not have their next click swallowed.
 */
export const SETTLE_MS = 600;

export interface AmbientFrostEnv {
	doc: Document;
	win: Window;
	/** Injectable clock (tests). Defaults to `Date.now`. */
	now?: () => number;
}

interface ListenerBinding {
	type: string;
	options: AddEventListenerOptions;
	handler: EventListener;
}

/**
 * Events that count as a "meaningful interaction". All are registered with
 * `capture: true` so they fire even when an inner component stops
 * propagation, and every scrollable/passive one is passive so scrolling is
 * never blocked or de-optimised.
 */
const TRIGGERS: ReadonlyArray<{ type: string; options: AddEventListenerOptions }> = [
	{ type: 'pointerdown', options: { capture: true, passive: true } },
	{ type: 'mousedown', options: { capture: true, passive: true } },
	{ type: 'touchstart', options: { capture: true, passive: true } },
	{ type: 'keydown', options: { capture: true } },
	{ type: 'wheel', options: { capture: true, passive: true } },
	{ type: 'scroll', options: { capture: true, passive: true } },
	{ type: 'click', options: { capture: true } },
	{ type: 'focusin', options: { capture: true } }
];

let env: AmbientFrostEnv | null = null;
let bindings: ListenerBinding[] = [];
let active = false;
let reducedMotion = false;
/** Timestamp after which events count as real user interactions. */
let armedAt = 0;

const nowMs = (): number => {
	if (env?.now) return env.now();
	return typeof Date.now === 'function' ? Date.now() : 0;
};

/** True once the post-load settle window has elapsed. */
export const isSettled = (): boolean => nowMs() - armedAt >= SETTLE_MS;

const readReducedMotion = (win: Window): boolean => {
	try {
		if (typeof win.matchMedia !== 'function') return false;
		return win.matchMedia('(prefers-reduced-motion: reduce)').matches === true;
	} catch {
		return false;
	}
};

const frostTargets = (e: AmbientFrostEnv): Element[] =>
	[e.doc.documentElement, e.doc.body].filter(Boolean) as Element[];

const applyActiveState = (e: AmbientFrostEnv): void => {
	for (const el of frostTargets(e)) {
		el.classList.add(FROST_CLASS);
		el.classList.toggle(FROST_INSTANT_CLASS, reducedMotion);
	}
};

const clearActiveState = (e: AmbientFrostEnv): void => {
	for (const el of frostTargets(e)) {
		el.classList.remove(FROST_CLASS);
		el.classList.remove(FROST_INSTANT_CLASS);
	}
};

const detach = (): void => {
	const current = env;
	if (current) {
		for (const binding of bindings) {
			current.doc.removeEventListener(binding.type, binding.handler, binding.options);
		}
	}
	bindings = [];
};

const attach = (guardSettle = false): void => {
	const current = env;
	if (!current || bindings.length > 0) return;

	// Guarded arming = page load: ignore events until the settle window has
	// passed. Unguarded arming = SPA navigation: the user is mid-interaction,
	// so their very next action must count (no swallowed clicks). -Infinity
	// (rather than 0) keeps this correct regardless of the clock's origin.
	armedAt = guardSettle ? nowMs() : Number.NEGATIVE_INFINITY;

	const handler: EventListener = () => {
		// Ignore the synthetic events browsers emit while a page/view is still
		// settling — a restored scroll position and autofocus arrive before the
		// user can possibly act, and would frost the background during load,
		// breaking the "clear glass on initial load" requirement.
		if (!isSettled()) {
			return;
		}
		activateAmbientFrost();
	};

	for (const { type, options } of TRIGGERS) {
		current.doc.addEventListener(type, handler, options);
		bindings.push({ type, options, handler });
	}
};

/**
 * Arm the controller for the current page view. Idempotent with respect to
 * listeners: calling it twice replaces the previous arming rather than
 * stacking listeners. Returns a cleanup function.
 */
export function initAmbientFrost(override?: Partial<AmbientFrostEnv>): () => void {
	const doc = override?.doc ?? (typeof document !== 'undefined' ? document : null);
	const win = override?.win ?? (typeof window !== 'undefined' ? window : null);

	if (!doc || !win) {
		return () => {};
	}

	detach();
	env = { doc, win, now: override?.now };
	active = false;
	reducedMotion = readReducedMotion(win);
	clearActiveState(env);
	// Page load → guarded arming, so synthetic load-time events (restored
	// scroll, autofocus) don't frost the background before the user acts.
	attach(true);

	return () => {
		teardownAmbientFrost();
	};
}

/**
 * Trigger the frost. Returns true when this call performed the activation and
 * false when the page was already frosted (or unarmed) — the idempotency
 * guarantee that stops a burst of events from restarting the transition.
 */
export function activateAmbientFrost(): boolean {
	const current = env;
	if (!current || active) return false;

	active = true;
	// Stop listening immediately: the state is one-way for this view.
	detach();
	applyActiveState(current);
	return true;
}

/**
 * Return to the clear-glass state and re-arm the trigger. Called on initial
 * load and on SPA navigation so a refresh or a newly opened report replays
 * the behaviour from the start.
 */
export function resetAmbientFrost(): void {
	const current = env;
	if (!current) return;

	active = false;
	reducedMotion = readReducedMotion(current.win);
	clearActiveState(current);
	attach();
}

/** Tear down listeners and state (unmount / hard reset). */
export function teardownAmbientFrost(): void {
	const current = env;
	detach();
	active = false;
	if (current) {
		clearActiveState(current);
	}
	env = null;
}

/** Whether the current view has already been frosted. */
export function isAmbientFrostActive(): boolean {
	return active;
}

/** Whether the controller is armed for a view. */
export function isAmbientFrostArmed(): boolean {
	return env !== null;
}
