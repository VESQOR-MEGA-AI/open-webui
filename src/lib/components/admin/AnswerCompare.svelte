<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { goto } from '$app/navigation';
	import dayjs from '$lib/dayjs';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';

	import {
		CompareApiError,
		CompareConnectionError,
		createRun,
		generateAnswer,
		getCompareConfig,
		getRun,
		listRuns,
		buildSummary,
		judgeRun,
		runAllJudges,
		type CreateRunResponse,
		type GetRunResponse,
		type ProviderConfig,
		type ApiErrorDetail,
		type ProviderId,
		type RunAllEntry,
		type RunRow
	} from '$lib/apis/answer-compare';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import AnswerCard from './AnswerCompare/AnswerCard.svelte';
	import JudgeCard from './AnswerCompare/JudgeCard.svelte';
	import TallyPanel from './AnswerCompare/TallyPanel.svelte';
	import {
		applyTally,
		clearTally,
		initialTallyState,
		type TallyPanelState
	} from './AnswerCompare/tallyState';
	import SummaryPanel from './AnswerCompare/SummaryPanel.svelte';
	import HistoryList from './AnswerCompare/HistoryList.svelte';
	import {
		appendHistory,
		applyHistory,
		initialHistoryState,
		lineageLines,
		olderCursor,
		RERUN_NOTICE_COPY,
		type HistoryState
	} from './AnswerCompare/historyState';
	import {
		applySummary,
		applySummaryRow,
		clearSummary,
		initialSummaryState,
		summaryButtonDisabledReason,
		type SummaryPanelState
	} from './AnswerCompare/summaryState';
	import {
		applyJudgeConfigs,
		applyJudgeFailure,
		applyJudgeNotConfigured,
		applyJudgeNotEnoughAnswers,
		applyJudgeOversized,
		applyJudgeResult,
		applyJudgeRunState,
		capableJudgeIds,
		initialJudgeCards,
		judgeButtonDisabledReason,
		judgesToRun,
		startJudging,
		type JudgeCardsState
	} from './AnswerCompare/judgeState';
	import {
		applyFailure,
		applyNotConfigured,
		applyOversized,
		applyProviderConfigs,
		applyResult,
		applyRunState,
		initialCards,
		PROVIDER_IDS,
		PROVIDER_LABELS,
		providersToGenerate,
		RECOVERY_DELAYS_MS,
		startGeneration,
		type CardsState
	} from './AnswerCompare/state';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let token = '';
	let prompt = '';
	let reference = '';

	let runId: string | null = null;
	let providers: ProviderConfig[] = [];
	let cards: CardsState = initialCards();
	let judgeCards: JudgeCardsState = initialJudgeCards();
	let tallyState: TallyPanelState = initialTallyState();
	let summaryState: SummaryPanelState = initialSummaryState();
	let historyState: HistoryState = initialHistoryState();
	/** The open run itself, for the lineage lines and the "nothing generated yet" notice. */
	let currentRun: RunRow | null = null;
	let configLoaded = false;
	let runAllInFlight = false;
	let summaryInFlight = false;

	/**
	 * The cost dialog (owner decision): both bulk actions confirm first, the
	 * single-provider and single-judge buttons do not — one click, one request.
	 * One ConfirmDialog instance, driven by whichever bulk action opened it.
	 */
	let showCostDialog = false;
	let pendingBulkAction: (() => void | Promise<void>) | null = null;
	let pendingRequestCount = 0;

	const confirmCost = (count: number, action: () => void | Promise<void>) => {
		pendingRequestCount = count;
		pendingBulkAction = action;
		showCostDialog = true;
	};

	const runPendingBulkAction = () => {
		const action = pendingBulkAction;
		pendingBulkAction = null;
		void action?.();
	};

	/** Cancel does nothing at all: no request, no card state change, no run created. */
	const cancelBulkAction = () => {
		pendingBulkAction = null;
		pendingRequestCount = 0;
	};

	/** Recovery timers, one per provider, so a cleanup can never miss one. */
	const recoveryTimers = new Map<ProviderId, ReturnType<typeof setTimeout>>();
	const judgeRecoveryTimers = new Map<ProviderId, ReturnType<typeof setTimeout>>();

	// Answers: every provider can generate one, so this walks PROVIDER_IDS.
	$: completeAnswers = PROVIDER_IDS.filter((provider) => cards[provider].answer !== null).length;
	// Judges: only the judge-capable providers get a button/card — VESQOR generates
	// an answer but never judges, so this walks capableJudgeIds, not PROVIDER_IDS.
	$: judgeReasons = capableJudgeIds(judgeCards).map((judge) => ({
		judge,
		reason: judgeButtonDisabledReason(judgeCards[judge], completeAnswers)
	}));
	$: anyJudgeConfigured = capableJudgeIds(judgeCards).some(
		(judge) => judgeCards[judge].missing === null
	);
	$: anyJudgeInFlight = capableJudgeIds(judgeCards).some((judge) => judgeCards[judge].inFlight);
	$: lineage = currentRun ? lineageLines(currentRun) : { parent: null, children: null };
	// A rerun generates nothing on purpose (the cost dialog owns that); say so, or
	// the empty cards after "Run again" read as a failure. It goes as soon as an
	// answer exists.
	$: showRerunNotice =
		currentRun !== null &&
		currentRun.rerun_of_run_id !== null &&
		completeAnswers === 0 &&
		!anyInFlight;
	$: includedVerdicts = tallyState.tally?.n_included ?? 0;
	$: summaryReason = !runId
		? $i18n.t('Enter a prompt and generate three answers to compare.')
		: summaryInFlight
			? $i18n.t('Generation in progress.')
			: (summaryButtonDisabledReason(includedVerdicts)?.key ?? '') &&
				$i18n.t(summaryButtonDisabledReason(includedVerdicts)?.key ?? '');
	$: runAllReason =
		completeAnswers < 2
			? $i18n.t('Judging unlocks once at least two answers exist.')
			: !anyJudgeConfigured
				? $i18n.t('No judge is configured yet.')
				: anyJudgeInFlight || runAllInFlight
					? $i18n.t('This judge is already running.')
					: '';

	$: anyInFlight = PROVIDER_IDS.some((provider) => cards[provider].inFlight);
	$: anyConfigured = providers.some((provider) => provider.configured);
	$: disabledReason = !prompt.trim()
		? $i18n.t('Enter a prompt first.')
		: anyInFlight
			? $i18n.t('Generation in progress.')
			: configLoaded && !anyConfigured
				? $i18n.t('No provider is configured yet.')
				: '';
	$: generateDisabled = disabledReason !== '';

	/**
	 * A failure of the call itself — our backend answering with a typed error, or
	 * the request never completing. Anything else (a TypeError in our own code,
	 * say) is a bug and must not be swallowed as "the network was down".
	 */
	const isCallFailure = (err: unknown): boolean =>
		err instanceof CompareApiError || err instanceof CompareConnectionError;

	const clearRecovery = (provider: ProviderId) => {
		const timer = recoveryTimers.get(provider);
		if (timer !== undefined) {
			clearTimeout(timer);
			recoveryTimers.delete(provider);
		}
	};

	const clearJudgeRecovery = (judge: ProviderId) => {
		const timer = judgeRecoveryTimers.get(judge);
		if (timer !== undefined) {
			clearTimeout(timer);
			judgeRecoveryTimers.delete(judge);
		}
	};

	onDestroy(() => {
		for (const provider of recoveryTimers.keys()) {
			clearRecovery(provider);
		}
		for (const judge of judgeRecoveryTimers.keys()) {
			clearJudgeRecovery(judge);
		}
	});

	/**
	 * Adopt everything a GET says, for answers and reports alike. Idempotent, so
	 * it is safe to call after any successful POST and from any recovery timer.
	 */
	const adoptRun = (stored: GetRunResponse) => {
		currentRun = stored.run;
		providers = stored.providers;
		cards = applyRunState(cards, stored.answers, stored.providers);
		judgeCards = applyJudgeRunState(judgeCards, stored.reports, stored.providers);
		// The tally re-renders from every GET: a regeneration shrinks it and marks
		// it partial with no separate refresh action.
		tallyState = applyTally(stored);
		summaryState = applySummary(stored);
	};

	/**
	 * `outdated` lives only in the GET wrapper. After ANY successful POST — an
	 * answer or a report — one GET, so a regeneration raises the banner and a
	 * re-judge clears it, without a reload and without the page guessing.
	 */
	const refreshRun = async () => {
		if (!runId) return;
		try {
			adoptRun(await getRun(token, runId));
		} catch (err) {
			// The POST already settled the card; a failed refresh only delays the
			// banner until the next successful call. Nothing to show for it — but
			// only for a call that actually failed: a bug of ours must not wear a
			// network failure's clothes.
			if (!isCallFailure(err)) throw err;
		}
	};

	const reportApiError = (provider: ProviderId, err: CompareApiError) => {
		const detail = err.detail;

		if (detail.code === 'not_configured') {
			cards = applyNotConfigured(cards, provider, (detail.missing as string[]) ?? []);
			return;
		}

		if (detail.code === 'oversized') {
			cards = applyOversized(
				cards,
				provider,
				Number(detail.limit_chars ?? 0),
				Number(detail.actual_chars ?? 0)
			);
			return;
		}

		cards = applyFailure(cards, provider, { code: detail.code });
	};

	/**
	 * We did not hear back, so we do not know what happened — and guessing
	 * "the provider failed" would be a lie the backend went out of its way to
	 * make unnecessary: it writes the `pending` row before calling the provider,
	 * so the answer may already be stored. Ask, a few times, before giving up.
	 */
	const recoverFromConnectionLoss = (provider: ProviderId, attempt = 0) => {
		clearRecovery(provider);

		if (!runId || attempt >= RECOVERY_DELAYS_MS.length) {
			if (cards[provider].inFlight) {
				cards = applyFailure(cards, provider, { code: 'network' });
			}
			return;
		}

		const timer = setTimeout(async () => {
			recoveryTimers.delete(provider);
			if (!runId) return;

			try {
				const stored = await getRun(token, runId);
				adoptRun(stored);
				// Still pending on the server: keep asking.
				if (cards[provider].inFlight) {
					recoverFromConnectionLoss(provider, attempt + 1);
				}
			} catch (err) {
				if (err instanceof CompareApiError && err.code === 'unauthorized') {
					cards = applyFailure(cards, provider, { code: 'unauthorized' });
					return;
				}
				recoverFromConnectionLoss(provider, attempt + 1);
			}
		}, RECOVERY_DELAYS_MS[attempt]);

		recoveryTimers.set(provider, timer);
	};

	/**
	 * One provider's whole lifecycle, start to finish, in its own async function
	 * with its own try/catch. Nothing here reads or writes another provider's
	 * card, and the state module replaces only this provider's entry — which is
	 * what keeps the three cards independent in the code and not just on screen.
	 */
	const runProvider = async (provider: ProviderId, id: string) => {
		clearRecovery(provider);

		try {
			const row = await generateAnswer(token, id, provider);
			cards = applyResult(cards, provider, row);
			if (row.status === 'complete') {
				await refreshRun();
			}
		} catch (err) {
			if (err instanceof CompareApiError) {
				reportApiError(provider, err);
				if (err.code === 'already_running') {
					// This state must have a way out: ask the server on the same
					// schedule as a connection loss rather than sitting here.
					recoverFromConnectionLoss(provider);
				}
				return;
			}

			if (err instanceof CompareConnectionError) {
				recoverFromConnectionLoss(provider);
				return;
			}

			cards = applyFailure(cards, provider, { code: 'network' });
		}
	};

	const startProvider = (provider: ProviderId, id: string) => {
		const config = providers.find((item) => item.id === provider);
		if (config && !config.configured) {
			cards = applyNotConfigured(cards, provider, config.missing);
			return Promise.resolve();
		}

		// Through the state module, like every other write to `cards`: the rule
		// that a rendered answer survives the start of a regeneration is tested
		// there, and an inline copy of this transition would not be covered by it.
		cards = startGeneration(cards, provider, Date.now());
		return runProvider(provider, id);
	};

	const reportJudgeApiError = (judge: ProviderId, err: CompareApiError) =>
		applyJudgeDetail(judge, err.detail);

	/** A typed detail — from an HTTP error or a run-all `skipped` entry — onto the judge's card. */
	const applyJudgeDetail = (judge: ProviderId, detail: ApiErrorDetail) => {
		if (detail.code === 'not_configured') {
			judgeCards = applyJudgeNotConfigured(judgeCards, judge, (detail.missing as string[]) ?? []);
			return;
		}
		if (detail.code === 'oversized') {
			judgeCards = applyJudgeOversized(
				judgeCards,
				judge,
				Number(detail.limit_chars ?? 0),
				Number(detail.actual_chars ?? 0)
			);
			return;
		}
		if (detail.code === 'not_enough_answers') {
			judgeCards = applyJudgeNotEnoughAnswers(
				judgeCards,
				judge,
				(detail.complete as ProviderId[]) ?? []
			);
			return;
		}
		judgeCards = applyJudgeFailure(judgeCards, judge, { code: detail.code });
	};

	/** Same rule as answers: the backend wrote the pending row first, so ask before guessing. */
	const recoverJudgeFromConnectionLoss = (judge: ProviderId, attempt = 0) => {
		clearJudgeRecovery(judge);

		if (!runId || attempt >= RECOVERY_DELAYS_MS.length) {
			if (judgeCards[judge].inFlight) {
				judgeCards = applyJudgeFailure(judgeCards, judge, { code: 'network' });
			}
			return;
		}

		const timer = setTimeout(async () => {
			judgeRecoveryTimers.delete(judge);
			if (!runId) return;
			try {
				adoptRun(await getRun(token, runId));
				if (judgeCards[judge].inFlight) {
					recoverJudgeFromConnectionLoss(judge, attempt + 1);
				}
			} catch (err) {
				if (err instanceof CompareApiError && err.code === 'unauthorized') {
					judgeCards = applyJudgeFailure(judgeCards, judge, { code: 'unauthorized' });
					return;
				}
				recoverJudgeFromConnectionLoss(judge, attempt + 1);
			}
		}, RECOVERY_DELAYS_MS[attempt]);

		judgeRecoveryTimers.set(judge, timer);
	};

	/** One judge's lifecycle, in its own function with its own try/catch. */
	const runJudge = async (judge: ProviderId) => {
		// `judgeButtonDisabledReason` only reaches the button on the next tick, so a
		// fast double-click would otherwise fire twice before the button disables.
		// The card's own in-flight flag is set synchronously and is the real guard;
		// the server's `already_running` 409 is the backstop, not the first line.
		if (!runId || judgeCards[judge]?.inFlight) return;
		clearJudgeRecovery(judge);
		judgeCards = startJudging(judgeCards, judge, Date.now());

		try {
			const row = await judgeRun(token, runId, judge);
			judgeCards = applyJudgeResult(judgeCards, judge, row);
			if (row.status === 'complete') {
				await refreshRun();
			}
		} catch (err) {
			if (err instanceof CompareApiError) {
				reportJudgeApiError(judge, err);
				if (err.code === 'already_running') {
					recoverJudgeFromConnectionLoss(judge);
				}
				return;
			}
			if (err instanceof CompareConnectionError) {
				recoverJudgeFromConnectionLoss(judge);
				return;
			}
			judgeCards = applyJudgeFailure(judgeCards, judge, { code: 'network' });
		}
	};

	/** One run-all entry lands on its judge's card through the existing transitions. */
	const applyRunAllEntry = (entry: RunAllEntry) => {
		const judge = entry.judge;
		if (entry.status === 'skipped' && entry.reason) {
			applyJudgeDetail(judge, entry.reason);
			return;
		}
		if (entry.report) {
			judgeCards = applyJudgeResult(judgeCards, judge, entry.report);
			return;
		}
		judgeCards = applyJudgeFailure(judgeCards, judge, { code: entry.reason?.code ?? 'internal' });
	};

	const runAll = async () => {
		// `runAllReason` is reactive and stale within the click's own tick; the
		// plain flag is not.
		if (!runId || runAllInFlight || anyJudgeInFlight || runAllReason) return;
		runAllInFlight = true;
		const started = Date.now();
		for (const judge of capableJudgeIds(judgeCards)) {
			clearJudgeRecovery(judge);
			if (judgeCards[judge].missing === null) {
				judgeCards = startJudging(judgeCards, judge, started);
			}
		}

		try {
			const response = await runAllJudges(token, runId);
			for (const entry of response.results) {
				applyRunAllEntry(entry);
			}
			tallyState = applyTally(response);
			// The 3b rule: one GET after any successful POST, for outdated and the authoritative tally.
			await refreshRun();
		} catch (err) {
			if (err instanceof CompareApiError) {
				// Global preconditions only: nothing ran, so every started card settles.
				for (const judge of capableJudgeIds(judgeCards)) {
					if (judgeCards[judge].inFlight) {
						if (err.code === 'not_enough_answers') {
							judgeCards = applyJudgeNotEnoughAnswers(
								judgeCards,
								judge,
								(err.detail.complete as ProviderId[]) ?? []
							);
						} else {
							judgeCards = applyJudgeFailure(judgeCards, judge, { code: err.code });
						}
					}
				}
			} else if (err instanceof CompareConnectionError) {
				// Three pending rows may already be written: ask, per judge, before guessing.
				for (const judge of capableJudgeIds(judgeCards)) {
					if (judgeCards[judge].inFlight) {
						recoverJudgeFromConnectionLoss(judge);
					}
				}
			} else {
				for (const judge of capableJudgeIds(judgeCards)) {
					if (judgeCards[judge].inFlight) {
						judgeCards = applyJudgeFailure(judgeCards, judge, { code: 'network' });
					}
				}
			}
		} finally {
			runAllInFlight = false;
		}
	};

	const loadHistory = async () => {
		try {
			historyState = applyHistory(await listRuns(token));
		} catch (err) {
			// The history is a convenience; a failure to fetch it must not break a
			// page that may have generation in flight. A TypeError from our own
			// code is not that, and is rethrown rather than read as "no history".
			if (!isCallFailure(err)) throw err;
		}
	};

	const loadOlderHistory = async () => {
		const cursor = olderCursor(historyState);
		if (!cursor) return;
		try {
			historyState = appendHistory(historyState, await listRuns(token, cursor));
		} catch (err) {
			// Same: the page keeps whatever is already listed.
			if (!isCallFailure(err)) throw err;
		}
	};

	/** Reopen through the existing path: set ?run= and adopt, no new loading logic. */
	const openHistoryRun = async (id: string) => {
		void goto(`/admin/compare?run=${encodeURIComponent(id)}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
		await openStoredRun(id);
	};

	/**
	 * "Run again" creates a new run from an earlier one and stops there. It does
	 * NOT generate: generation stays behind the cost dialog (DECISIONS.md#015),
	 * and a rerun that silently fired three paid requests would defeat the
	 * confirmation the owner just asked for.
	 */
	const rerunFrom = async (id: string) => {
		let created: CreateRunResponse;
		try {
			created = await createRun(token, { rerun_of_run_id: id });
		} catch (err) {
			toast.error(
				err instanceof CompareApiError
					? $i18n.t('The provider failed: {{code}}.', { code: err.code })
					: $i18n.t('Could not reach the provider.')
			);
			return;
		}
		await openHistoryRun(created.run.id);
		await loadHistory();
	};

	/** Assembles server-side from evidence already gathered: no provider call, no dialog. */
	const buildRunSummary = async () => {
		if (!runId || summaryInFlight || summaryReason) return;
		summaryInFlight = true;
		try {
			summaryState = applySummaryRow(summaryState, await buildSummary(token, runId));
			// The 3b rule: one GET after a successful POST, so `outdated` is right.
			await refreshRun();
		} catch (err) {
			if (err instanceof CompareApiError) {
				toast.error(
					err.code === 'no_verdicts_to_summarise'
						? $i18n.t('Summary needs at least one valid verdict.')
						: $i18n.t('The provider failed: {{code}}.', { code: err.code })
				);
			} else {
				toast.error($i18n.t('Could not reach the provider.'));
			}
		} finally {
			summaryInFlight = false;
		}
	};

	const generateAll = async () => {
		if (generateDisabled) return;

		let created: CreateRunResponse;
		try {
			created = await createRun(token, {
				prompt: prompt.trim(),
				reference: reference.trim() === '' ? null : reference
			});
		} catch (err) {
			if (err instanceof CompareApiError) {
				toast.error(
					err.code === 'unauthorized'
						? $i18n.t('Your session expired or admin access is required.')
						: $i18n.t('The provider failed: {{code}}.', { code: err.code })
				);
			} else {
				toast.error($i18n.t('Could not reach the provider.'));
			}
			return;
		}

		runId = created.run.id;
		providers = created.providers;
		cards = applyProviderConfigs(initialCards(), created.providers);
		judgeCards = applyJudgeConfigs(initialJudgeCards(created.providers), created.providers);
		tallyState = clearTally();
		summaryState = clearSummary();
		// The run id lives in the URL so a reload reopens the run being looked at
		// rather than dropping it. Full history is stage 6.
		void goto(`/admin/compare?run=${encodeURIComponent(created.run.id)}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
		void loadHistory();

		const oversized = new Set(
			created.input_size.per_provider.filter((item) => item.exceeds).map((item) => item.provider)
		);
		for (const item of created.input_size.per_provider) {
			if (item.exceeds) {
				cards = applyOversized(cards, item.provider, item.limit_chars, created.input_size.chars);
			}
		}

		// Fired in parallel; allSettled so one rejection cannot abort the others.
		await Promise.allSettled(
			PROVIDER_IDS.filter((provider) => !oversized.has(provider)).map((provider) =>
				startProvider(provider, created.run.id)
			)
		);
	};

	const retry = (provider: ProviderId) => {
		if (!runId) return;
		void startProvider(provider, runId);
	};

	/** Reopen the run named in the URL, adopting whatever the server has stored. */
	const openStoredRun = async (id: string) => {
		try {
			const stored = await getRun(token, id);
			runId = stored.run.id;
			prompt = stored.run.prompt;
			reference = stored.run.reference ?? '';
			providers = stored.providers;
			cards = applyRunState(
				applyProviderConfigs(initialCards(), stored.providers),
				stored.answers,
				stored.providers
			);
			judgeCards = applyJudgeRunState(
				applyJudgeConfigs(initialJudgeCards(stored.providers), stored.providers),
				stored.reports,
				stored.providers
			);

			// Anything the server still reports as pending keeps being asked about.
			// Walks PROVIDER_IDS because it checks both an answer and a report per
			// provider; a non-capable judge's card is simply never inFlight here.
			for (const provider of PROVIDER_IDS) {
				if (cards[provider].inFlight) {
					recoverFromConnectionLoss(provider);
				}
				if (judgeCards[provider].inFlight) {
					recoverJudgeFromConnectionLoss(provider);
				}
			}
		} catch (err) {
			if (err instanceof CompareApiError && err.code === 'unauthorized') {
				toast.error($i18n.t('Your session expired or admin access is required.'));
			} else if (err instanceof CompareApiError && err.code === 'run_not_found') {
				toast.error($i18n.t('This run is no longer available.'));
			} else {
				toast.error($i18n.t('Could not reach the provider.'));
			}
		}
	};

	onMount(async () => {
		token = localStorage.token ?? '';
		try {
			const config = await getCompareConfig(token);
			providers = config.providers;
			cards = applyProviderConfigs(initialCards(), config.providers);
			judgeCards = applyJudgeConfigs(initialJudgeCards(config.providers), config.providers);
		} catch (err) {
			if (err instanceof CompareApiError && err.code === 'unauthorized') {
				toast.error($i18n.t('Your session expired or admin access is required.'));
			} else {
				toast.error($i18n.t('Could not reach the provider.'));
			}
		} finally {
			configLoaded = true;
		}

		await loadHistory();

		const openRun = $page.url.searchParams.get('run');
		if (openRun) {
			await openStoredRun(openRun);
		}
	});
</script>

<div class="flex flex-col gap-4 px-4 py-3 min-w-0">
	<div class="flex flex-col gap-1">
		<div class="text-lg font-medium">{$i18n.t('Answer comparison')}</div>
		<div class="text-xs text-gray-500 max-w-3xl">
			{$i18n.t(
				'Answers are generated from one identical prompt. Judging happens blind — judges never see which system wrote which answer.'
			)}
		</div>
	</div>

	<!-- Collapsed while a run is open, expanded when there is none to look at. -->
	<HistoryList
		history={historyState}
		open={runId === null}
		onOpenRun={openHistoryRun}
		onRerun={rerunFrom}
		onLoadOlder={loadOlderHistory}
	/>

	<!-- A visible border, explicit text colours and a label on each field: the
	     page sits on a dark themed background, and a fill-only textarea with no
	     border reads as "there is no field here". This must hold without any
	     page-level stylesheet — vq25.css skins it further, but legibility is
	     not allowed to depend on that. -->
	<div class="flex flex-col gap-3">
		<label class="flex flex-col gap-1">
			<span class="text-xs font-medium text-gray-600 dark:text-gray-300">{$i18n.t('Prompt')}</span>
			<textarea
				class="w-full text-sm rounded-lg px-3 py-2 bg-transparent border border-gray-300 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 outline-hidden resize-y min-h-24"
				bind:value={prompt}
				placeholder={$i18n.t('The same prompt goes to all three systems')}
			></textarea>
		</label>
		<label class="flex flex-col gap-1">
			<span class="text-xs font-medium text-gray-600 dark:text-gray-300"
				>{$i18n.t('Reference material')}
				<span class="font-normal text-gray-400 dark:text-gray-500">({$i18n.t('optional')})</span
				></span
			>
			<textarea
				class="w-full text-sm rounded-lg px-3 py-2 bg-transparent border border-gray-300 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 outline-hidden resize-y min-h-16"
				bind:value={reference}
				placeholder={$i18n.t('Context the systems should rely on')}
			></textarea>
		</label>
	</div>

	{#if lineage.parent || lineage.children}
		<div class="flex flex-wrap items-center gap-3 text-xs text-gray-500">
			{#if lineage.parent && currentRun?.rerun_of}
				<button
					class="underline underline-offset-2 hover:text-gray-700 dark:hover:text-gray-300 transition"
					on:click={() => openHistoryRun(currentRun?.rerun_of?.run_id ?? '')}
				>
					{$i18n.t('Rerun of {{when}}', {
						when: dayjs.unix(currentRun.rerun_of.created_at).format('D MMM HH:mm')
					})}
				</button>
			{/if}
			{#if lineage.children}
				<span>{$i18n.t(lineage.children.key, lineage.children.params)}</span>
			{/if}
		</div>
	{/if}

	{#if showRerunNotice}
		<div class="text-xs text-gray-500">{$i18n.t(RERUN_NOTICE_COPY)}</div>
	{/if}

	<div class="flex flex-wrap items-center gap-3">
		<button
			class="px-3.5 py-1.5 text-sm font-medium rounded-lg bg-black text-white dark:bg-white dark:text-black transition disabled:opacity-40 disabled:cursor-not-allowed"
			disabled={generateDisabled}
			title={disabledReason}
			on:click={() => confirmCost(providersToGenerate(providers).length, generateAll)}
		>
			{$i18n.t('Generate all three answers')}
		</button>

		<!-- Every disabled control states its reason, visibly and not only on hover. -->
		{#if disabledReason}
			<span class="text-xs text-gray-500">{disabledReason}</span>
		{/if}
	</div>

	{#if runId === null}
		<div class="text-sm text-gray-500">
			{$i18n.t('Enter a prompt and generate three answers to compare.')}
		</div>
	{:else}
		<!-- Fixed column order, stacked below the breakpoint, equal widths above it. -->
		<div class="grid grid-cols-1 lg:grid-cols-3 gap-3 items-start min-w-0">
			{#each PROVIDER_IDS as provider (provider)}
				<AnswerCard card={cards[provider]} onRetry={() => retry(provider)} />
			{/each}
		</div>
		<div class="text-xs text-gray-500">
			{$i18n.t('Generating again keeps the current answer as an earlier version.')}
		</div>

		<!-- Judging: three secondary buttons, then three independent report cards. -->
		<div class="flex flex-col gap-3 pt-2">
			<div class="text-base font-medium">{$i18n.t('Judging')}</div>

			<div class="flex flex-wrap items-center gap-2">
				<!-- Strongest within the section, still secondary to "Generate all three answers". -->
				<Tooltip
					content={runAllReason ||
						$i18n.t('Judging again keeps the current report as an earlier version.')}
				>
					<button
						class="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-800 dark:border-gray-200 hover:bg-gray-50 dark:hover:bg-gray-850 transition disabled:opacity-40 disabled:cursor-not-allowed"
						disabled={runAllReason !== ''}
						on:click={() => confirmCost(judgesToRun(judgeCards).length, runAll)}
					>
						{$i18n.t('Run all three judges')}
					</button>
				</Tooltip>
				<Tooltip content={summaryReason || $i18n.t('Summary')}>
					<button
						class="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-850 transition disabled:opacity-40 disabled:cursor-not-allowed"
						disabled={summaryReason !== ''}
						on:click={buildRunSummary}
					>
						{$i18n.t('Build summary')}
					</button>
				</Tooltip>
				{#each judgeReasons as item (item.judge)}
					<Tooltip
						content={item.reason
							? $i18n.t(item.reason.key, item.reason.params)
							: $i18n.t('Judging again keeps the current report as an earlier version.')}
					>
						<button
							class="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-850 transition disabled:opacity-40 disabled:cursor-not-allowed"
							disabled={item.reason !== null}
							on:click={() => runJudge(item.judge)}
						>
							{$i18n.t('Judge with {{name}}', { name: PROVIDER_LABELS[item.judge] })}
						</button>
					</Tooltip>
				{/each}
			</div>

			<!-- Every disabled control states its reason, visibly and not only on hover. -->
			{#if runAllReason}
				<div class="text-xs text-gray-500">{runAllReason}</div>
			{/if}
			{#each judgeReasons.filter((item) => item.reason !== null) as item (item.judge)}
				<div class="text-xs text-gray-500">
					{PROVIDER_LABELS[item.judge]}: {$i18n.t(
						item.reason?.key ?? '',
						item.reason?.params ?? {}
					)}
				</div>
			{/each}

			<div class="grid grid-cols-1 lg:grid-cols-3 gap-3 items-start min-w-0">
				{#each capableJudgeIds(judgeCards) as judge (judge)}
					<JudgeCard card={judgeCards[judge]} onRetry={() => runJudge(judge)} />
				{/each}
			</div>

			{#if tallyState.tally}
				<TallyPanel tally={tallyState.tally} />
			{/if}

			{#if summaryReason}
				<div class="text-xs text-gray-500">{summaryReason}</div>
			{/if}
			{#if summaryState.summary}
				<SummaryPanel summary={summaryState.summary} />
			{/if}
		</div>
	{/if}
</div>

<!-- One dialog, both bulk actions. The count is what will really be sent. -->
<ConfirmDialog
	bind:show={showCostDialog}
	title={$i18n.t('Paid requests')}
	message={$i18n.t('This will send {{count}} paid AI requests. Continue?', {
		count: pendingRequestCount
	})}
	confirmLabel={$i18n.t('Continue')}
	cancelLabel={$i18n.t('Cancel')}
	on:confirm={runPendingBulkAction}
	on:cancel={cancelBulkAction}
/>
