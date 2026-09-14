<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { fade } from 'svelte/transition';
	import { toast } from 'svelte-sonner';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyToClipboard } from '$lib/utils';

	import ReportBody from './ReportBody.svelte';
	import { PROVIDER_LABELS, SLOW_GENERATION_AFTER_SECONDS } from './state';
	import {
		describeJudgeFailure,
		isJudgeRetryDisabled,
		judgeElapsedSeconds,
		judgePhase,
		nameWithLabel,
		reportToText,
		type JudgeCardState
	} from './judgeState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let card: JudgeCardState;
	export let onRetry: () => void;

	/** This card's own ticker; three cards, three intervals, each cleaning up after itself. */
	let now = Date.now();
	let ticker: ReturnType<typeof setInterval> | null = null;

	const stopTicker = () => {
		if (ticker !== null) {
			clearInterval(ticker);
			ticker = null;
		}
	};

	$: if (card.inFlight && ticker === null) {
		now = Date.now();
		ticker = setInterval(() => {
			now = Date.now();
		}, 1000);
	} else if (!card.inFlight && ticker !== null) {
		stopTicker();
	}

	onDestroy(stopTicker);

	$: phase = judgePhase(card);
	$: elapsed = judgeElapsedSeconds(card, now);
	$: failureCopy = card.failure ? describeJudgeFailure(card.failure) : null;
	$: retryDisabled = isJudgeRetryDisabled(card);
	$: retryLabel = card.report ? $i18n.t('Judge again') : $i18n.t('Retry');
	$: retryDisabledReason = !retryDisabled
		? $i18n.t('Judging again keeps the current report as an earlier version.')
		: card.inFlight
			? $i18n.t('This judge is already running.')
			: card.failure
				? $i18n.t(describeJudgeFailure(card.failure).key, describeJudgeFailure(card.failure).params)
				: $i18n.t('This judge is already running.');

	$: labelMap = card.report?.label_map ?? null;
	$: compromised = (card.report?.blinding_compromised ?? []).map((p) => nameWithLabel(p, labelMap));
	$: missing = (card.report?.missing_providers ?? []).map((p) => PROVIDER_LABELS[p]);

	const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';
	let reducedMotion = false;
	let motionQuery: MediaQueryList | null = null;
	const readMotionPreference = () => {
		reducedMotion = motionQuery?.matches ?? false;
	};
	onMount(() => {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
		motionQuery = window.matchMedia(REDUCED_MOTION);
		readMotionPreference();
		motionQuery.addEventListener('change', readMotionPreference);
	});
	onDestroy(() => {
		motionQuery?.removeEventListener('change', readMotionPreference);
	});
	$: fadeParams = reducedMotion ? { duration: 0 } : { duration: 150 };

	// A report is copyable as plain text, matching what the card shows. Only
	// offered when there is a report: copying "Not judged yet." helps nobody, and
	// the header control stays absent until there is something to copy.
	$: canCopy = card.report !== null;

	const copy = async () => {
		if (!card.report) return;
		await copyToClipboard(reportToText(card.judge, card.report, labelMap));
		toast.success($i18n.t('Copying to clipboard was successful!'));
	};
</script>

<div
	class="flex flex-col min-w-0 rounded-xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900"
>
	<div
		class="flex items-center justify-between gap-2 px-3.5 py-2.5 border-b border-gray-100 dark:border-gray-850"
	>
		<div class="text-sm font-medium truncate">
			{$i18n.t('Judge: {{name}}', { name: PROVIDER_LABELS[card.judge] })}
		</div>

		<div class="flex items-center gap-1.5 shrink-0">
			{#if canCopy}
				<Tooltip content={$i18n.t('Copy report')}>
					<button
						class="px-2 py-1 text-xs rounded-lg bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={copy}
					>
						{$i18n.t('Copy')}
					</button>
				</Tooltip>
			{/if}

			{#if phase !== 'requires_configuration' && phase !== 'empty'}
				<Tooltip content={retryDisabledReason}>
					<button
						class="px-2 py-1 text-xs rounded-lg bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 transition disabled:opacity-40 disabled:cursor-not-allowed"
						disabled={retryDisabled}
						on:click={onRetry}
					>
						{retryLabel}
					</button>
				</Tooltip>
			{/if}
		</div>
	</div>

	<div class="flex flex-col gap-2 px-3.5 py-3 min-w-0">
		{#if phase === 'requires_configuration'}
			<div class="text-sm font-medium">{$i18n.t('Requires configuration')}</div>
			<ul class="text-xs font-mono text-gray-600 dark:text-gray-400 break-all">
				{#each card.missing ?? [] as variable}
					<li>{variable}</li>
				{/each}
			</ul>
			<div class="text-xs text-gray-500">{$i18n.t('Set these in the environment and restart.')}</div>
		{:else}
			<!-- Progress is a strip ABOVE the report, never a spinner over it. -->
			{#if card.inFlight}
				<div
					class="flex items-center gap-2 text-xs text-gray-500 {card.report
						? 'pb-2 border-b border-gray-100 dark:border-gray-850'
						: ''}"
				>
					<Spinner className="size-3.5" />
					<span>{card.report ? $i18n.t('Judging again…') : $i18n.t('Judging…')}</span>
					<span class="tabular-nums">{elapsed}s</span>
				</div>
				{#if elapsed >= SLOW_GENERATION_AFTER_SECONDS}
					<div class="text-xs text-gray-500">{$i18n.t('Reasoning models can take a few minutes.')}</div>
				{/if}
			{/if}

			{#if failureCopy}
				<div class="text-xs rounded-lg px-2.5 py-2 bg-red-500/10 text-red-600 dark:text-red-400 break-words">
					{$i18n.t(failureCopy.key, failureCopy.params)}
				</div>
			{/if}

			{#if card.report}
				{#if card.outdated}
					<div class="text-xs rounded-lg px-2.5 py-2 bg-amber-500/10 text-amber-700 dark:text-amber-400">
						{$i18n.t('Based on earlier answer versions. Judge again to evaluate the current answers.')}
					</div>
				{/if}

				<div class="text-xs text-gray-500">
					{$i18n.t(
						'This judge saw the answers as A, B, C in a random order and was not told which system wrote which.'
					)}
				</div>
				{#each compromised as item (item.provider)}
					<div class="text-xs text-gray-500">
						{$i18n.t(
							'{{provider}} named its own source in its answer, so blinding was compromised for that answer.',
							{ provider: item.label ? `${item.name} (was ${item.label})` : item.name }
						)}
					</div>
				{/each}
				{#each missing as name (name)}
					<div class="text-xs text-gray-500">
						{$i18n.t('{{provider}} had no answer when this judge ran.', { provider: name })}
					</div>
				{/each}

				<div class="report-body min-w-0" in:fade={fadeParams}>
					{#if card.report.mapped}
						<ReportBody mapped={card.report.mapped} {labelMap} />
					{:else}
						<div class="text-xs text-gray-500">
							{$i18n.t('This report could not be mapped to provider names. The raw report is kept.')}
						</div>
					{/if}
				</div>
			{:else if !card.inFlight && !failureCopy}
				<div class="text-sm text-gray-500">{$i18n.t('Not judged yet.')}</div>
			{/if}
		{/if}
	</div>

	{#if card.report}
		<div
			class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2 text-xs text-gray-500 border-t border-gray-100 dark:border-gray-850"
		>
			{#if card.report.model}
				<span class="truncate">{card.report.model}</span>
			{/if}
			<span>{$i18n.t('Version {{revision}}', { revision: card.report.revision })}</span>
		</div>
	{/if}
</div>

<style>
	.report-body {
		max-height: 40rem;
		overflow-y: auto;
	}
</style>
