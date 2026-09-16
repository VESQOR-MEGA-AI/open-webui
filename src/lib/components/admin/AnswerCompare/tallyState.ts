import type { GeneratorId, JudgeId, JudgeReports, ProviderId, Tally } from '$lib/apis/answer-compare';
import { GENERATOR_IDS, JUDGE_IDS, PROVIDER_LABELS } from './state';

/**
 * Tally panel state, a sibling of `judgeState.ts` with the same discipline:
 * pure transitions, one write point, the page writes only through here.
 *
 * The one rule: **the panel never counts.** The server computes the tally from
 * validated, fresh verdicts (one implementation, shared with the persisted
 * summary); this module stores what the server sent and turns it into copy.
 * `applyTally` is handed the whole GET/run-all response — reports included — so
 * that a version of it that recounted from the reports would be *possible*, and
 * the test that feeds it a tally disagreeing with those reports would catch it.
 */

export interface TallyPanelState {
	tally: Tally | null;
}

export const initialTallyState = (): TallyPanelState => ({ tally: null });

/** The single write point. */
const withTally = (tally: Tally | null): TallyPanelState => ({ tally });

/**
 * Adopt the server's tally from a response that carries one. The reports in the
 * same response are deliberately NOT consulted: the tally is authoritative.
 */
export const applyTally = (response: { tally?: Tally | null; reports?: JudgeReports[] }): TallyPanelState =>
	withTally(response.tally ?? null);

export const clearTally = (): TallyPanelState => withTally(null);

/** Plain-words copy per exclusion reason. Keys are i18n keys (= English text). */
export const EXCLUSION_COPY: Record<string, string> = {
	no_report: 'not judged',
	outdated: 'report is outdated (answers changed)',
	failed: 'report failed',
	malformed: 'report could not be read',
	not_configured: 'requires configuration',
	unmappable: 'report could not be mapped to provider names'
};

export const AGREEMENT_LINE = 'Agreement between judges is not evidence of correctness.';

/** Why a lone verdict yields no preferred answer (DECISIONS.md#014). */
export const SINGLE_JUDGE_COPY = 'Only one judge has a valid verdict — a preferred answer requires at least two.';

export interface Copy {
	key: string;
	params: Record<string, string | number>;
}

export interface VoteLine {
	provider: GeneratorId;
	name: string;
	votes: number;
}

export interface ExcludedLine {
	judge: JudgeId;
	name: string;
	reason: Copy;
	/** The failed re-judge riding on an outdated entry, when present. */
	latestAttempt: Copy | null;
	/** An outdated self-vote, shown as a small note in the excluded list. */
	selfVote: Copy | null;
}

export interface VerdictLine {
	judge: JudgeId;
	/** A tie or an inconclusive verdict, in plain words. Null for a sole winner. */
	text: Copy | null;
	/** The self-vote annotation, visibly a note beside the verdict. */
	selfVote: Copy | null;
}

export interface TallyView {
	headline: Copy;
	votes: VoteLine[];
	partial: Copy | null;
	excluded: ExcludedLine[];
	verdicts: VerdictLine[];
	footer: Copy;
}

const name = (provider: ProviderId): string => PROVIDER_LABELS[provider];
const names = (providers: GeneratorId[]): string => providers.map(name).join(', ');

const headlineOf = (tally: Tally): Copy => {
	const outcome = tally.outcome;
	if (outcome.kind === 'preferred' && outcome.provider) {
		// The second number is n_included, not 3: a "1 of 1" partial tally must
		// not read as "1 of 3".
		return {
			key: 'Preferred answer: {{provider}} ({{votes}} of {{nIncluded}} judges)',
			params: {
				provider: name(outcome.provider),
				votes: tally.votes[outcome.provider] ?? 0,
				nIncluded: tally.n_included
			}
		};
	}
	if (outcome.kind === 'no_majority') {
		// One judge naming a winner but no preferred answer reads as a bug unless
		// the reason is stated (owner decision, DECISIONS.md#014).
		if (tally.n_included === 1) {
			return { key: SINGLE_JUDGE_COPY, params: {} };
		}
		return { key: 'No majority', params: {} };
	}
	return { key: 'No valid verdicts yet', params: {} };
};

const selfVoteCopy = (kind: string, judge: JudgeId): Copy =>
	kind === 'tie'
		? { key: '{{judge}} included its own answer in a tie', params: { judge: name(judge) } }
		: { key: '{{judge}} judged its own answer the winner', params: { judge: name(judge) } };

/** Everything the panel shows, derived from the tally alone. */
export const tallyView = (tally: Tally): TallyView => {
	const selfVotes = new Map(tally.self_votes.map((item) => [item.judge, item.kind]));
	const selfVotesExcluded = new Map(tally.self_votes_excluded.map((item) => [item.judge, item.kind]));
	const ties = new Map(tally.ties.map((item) => [item.judge, item.providers]));
	const inconclusive = new Set(tally.inconclusive);

	return {
		headline: headlineOf(tally),
		votes: GENERATOR_IDS.map((provider) => ({
			provider,
			name: name(provider),
			votes: tally.votes[provider] ?? 0
		})),
		partial: tally.partial
			? {
					key: 'Partial: {{nIncluded}} of {{total}} judges included',
					params: { nIncluded: tally.n_included, total: JUDGE_IDS.length }
				}
			: null,
		excluded: tally.excluded.map((entry) => ({
			judge: entry.judge,
			name: name(entry.judge),
			reason: { key: EXCLUSION_COPY[entry.reason] ?? entry.reason, params: {} },
			latestAttempt: entry.latest_attempt
				? {
						key: entry.latest_attempt === 'malformed' ? 'latest re-judge could not be read' : 'latest re-judge failed',
						params: {}
					}
				: null,
			selfVote: selfVotesExcluded.has(entry.judge)
				? selfVoteCopy(selfVotesExcluded.get(entry.judge) as string, entry.judge)
				: null
		})),
		verdicts: tally.verdicts.map((verdict): VerdictLine => {
			let text: Copy | null = null;
			if (ties.has(verdict.judge)) {
				text = {
					key: '{{judge}} judged a tie between {{providers}}.',
					params: { judge: name(verdict.judge), providers: names(ties.get(verdict.judge) ?? []) }
				};
			} else if (inconclusive.has(verdict.judge)) {
				text = { key: '{{judge}} found no reliable winner.', params: { judge: name(verdict.judge) } };
			}
			return {
				judge: verdict.judge,
				text,
				selfVote: selfVotes.has(verdict.judge) ? selfVoteCopy(selfVotes.get(verdict.judge) as string, verdict.judge) : null
			};
		}),
		footer: { key: AGREEMENT_LINE, params: {} }
	};
};
