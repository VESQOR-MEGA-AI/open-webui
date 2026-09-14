import { describe, expect, it } from 'vitest';
import type { RunListItem, RunListResponse, RunRow } from '$lib/apis/answer-compare';
import {
	appendHistory,
	applyHistory,
	clearHistory,
	EMPTY_HISTORY_COPY,
	hasOlder,
	historyRows,
	initialHistoryState,
	lineageLines,
	olderCursor,
	RERUN_NOTICE_COPY
} from './historyState';

const run = (overrides: Partial<RunListItem> = {}): RunListItem => ({
	id: 'run-1',
	created_at: 1000,
	admin_email: 'admin@example.com',
	prompt_excerpt: 'Which is faster?',
	prompt_truncated: false,
	has_reference: false,
	rerun_of_run_id: null,
	answers: { complete: 3, failed_attempts: 0, pending: 0 },
	reports: { complete: 2, failed_attempts: 0 },
	has_summary: false,
	...overrides
});

const page = (runs: RunListItem[], before: number | null = null, beforeId: string | null = null): RunListResponse => ({
	runs,
	next_before: before,
	next_before_id: beforeId
});

describe('the module renders counts, it never recounts', () => {
	it('shows the counts the server sent even when nothing else in the row agrees', () => {
		// The excerpt describes a run with no answers at all and the row claims a
		// summary; the counts say three answers, one failed attempt, two reports.
		// A module that re-derived anything from the row would disagree with them.
		const contradicting = run({
			prompt_excerpt: 'nothing was ever generated for this run',
			has_summary: true,
			answers: { complete: 3, failed_attempts: 1, pending: 2 },
			reports: { complete: 2, failed_attempts: 1 }
		});

		const [row] = historyRows(applyHistory(page([contradicting])));

		expect(row.chips).toEqual([
			{ key: '{{count}} answers', params: { count: 3 } },
			{ key: '{{count}} failed', params: { count: 1 } },
			{ key: '{{count}} pending', params: { count: 2 } },
			{ key: '{{count}} reports', params: { count: 2 } },
			{ key: '{{count}} reports failed', params: { count: 1 } },
			{ key: 'summary', params: {} }
		]);
		// And the excerpt is the server's string, not a recomputed one.
		expect(row.excerpt).toBe(contradicting.prompt_excerpt);
	});

	it('omits the optional chips when their counts are zero', () => {
		const [row] = historyRows(applyHistory(page([run()])));
		expect(row.chips.map((chip) => chip.key)).toEqual(['{{count}} answers', '{{count}} reports']);
	});

	it('a complete answer and a failed latest attempt are both shown', () => {
		// The project's rule: revision 1 complete plus revision 2 failed is one of
		// each. Hiding either half is the bug this shape exists to prevent.
		const [row] = historyRows(
			applyHistory(page([run({ answers: { complete: 1, failed_attempts: 1, pending: 0 } })]))
		);
		expect(row.chips[0]).toEqual({ key: '{{count}} answers', params: { count: 1 } });
		expect(row.chips[1]).toEqual({ key: '{{count}} failed', params: { count: 1 } });
	});

	it('passes the excerpt and its truncation flag straight through', () => {
		const long = run({ prompt_excerpt: 'x'.repeat(200), prompt_truncated: true, has_reference: true });
		const [row] = historyRows(applyHistory(page([long])));
		expect(row.excerpt).toBe(long.prompt_excerpt);
		expect(row.truncated).toBe(true);
		expect(row.hasReference).toBe(true);
		expect(row.adminEmail).toBe('admin@example.com');
	});

	it('marks a rerun row', () => {
		const [plain, rerun] = historyRows(
			applyHistory(page([run({ id: 'a' }), run({ id: 'b', rerun_of_run_id: 'a' })]))
		);
		expect(plain.isRerun).toBe(false);
		expect(rerun.isRerun).toBe(true);
	});
});

describe('paging appends and needs both halves of the cursor', () => {
	it('the first page replaces, a later page appends in order', () => {
		const first = applyHistory(page([run({ id: 'a' }), run({ id: 'b' })], 1000, 'b'));
		expect(first.runs.map((r) => r.id)).toEqual(['a', 'b']);
		expect(hasOlder(first)).toBe(true);
		expect(olderCursor(first)).toEqual({ before: 1000, before_id: 'b' });

		const second = appendHistory(first, page([run({ id: 'c' })]));
		expect(second.runs.map((r) => r.id)).toEqual(['a', 'b', 'c']);
		expect(hasOlder(second)).toBe(false);
		expect(olderCursor(second)).toBeNull();
	});

	it('never lists the same run twice', () => {
		const first = applyHistory(page([run({ id: 'a' }), run({ id: 'b' })], 1000, 'b'));
		const overlapping = appendHistory(first, page([run({ id: 'b' }), run({ id: 'c' })]));
		expect(overlapping.runs.map((r) => r.id)).toEqual(['a', 'b', 'c']);
	});

	it('offers no older page when either half of the cursor is missing', () => {
		// Seconds alone would lose same-second rows, so half a cursor is no cursor.
		expect(hasOlder(applyHistory(page([run()], 1000, null)))).toBe(false);
		expect(hasOlder(applyHistory(page([run()], null, 'b')))).toBe(false);
		expect(olderCursor(applyHistory(page([run()], 1000, null)))).toBeNull();
	});

	it('starts empty and clears back to empty', () => {
		const state = initialHistoryState();
		expect(state.runs).toEqual([]);
		expect(state.loaded).toBe(false);
		expect(hasOlder(state)).toBe(false);

		const loaded = applyHistory(page([]));
		expect(loaded.loaded).toBe(true);
		expect(loaded.runs).toEqual([]);
		expect(clearHistory()).toEqual(initialHistoryState());
		expect(EMPTY_HISTORY_COPY).toBe('No saved comparisons yet.');
	});
});

describe('lineage', () => {
	const runRow = (overrides: Partial<RunRow> = {}): RunRow => ({
		id: 'run-2',
		prompt: 'p',
		reference: null,
		status: 'active',
		rerun_of_run_id: null,
		created_at: 2000,
		rerun_of: null,
		rerun_count: 0,
		...overrides
	});

	it('names the source run and how many children it has', () => {
		const child = lineageLines(runRow({ rerun_of: { run_id: 'run-1', created_at: 1500 }, rerun_of_run_id: 'run-1' }));
		expect(child.parent).toEqual({ key: 'Rerun of {{when}}', params: { when: 1500 } });
		expect(child.children).toBeNull();

		const parent = lineageLines(runRow({ rerun_count: 2 }));
		expect(parent.parent).toBeNull();
		expect(parent.children).toEqual({ key: 'Re-run {{count}} times', params: { count: 2 } });
	});

	it('shows no parent line when the source no longer resolves', () => {
		const orphan = lineageLines(runRow({ rerun_of_run_id: 'gone', rerun_of: null }));
		expect(orphan.parent).toBeNull();
	});

	it('states plainly that a rerun generated nothing', () => {
		expect(RERUN_NOTICE_COPY).toBe(
			'New run created from the earlier prompt. Nothing has been generated yet — use Generate all three answers.'
		);
	});
});
