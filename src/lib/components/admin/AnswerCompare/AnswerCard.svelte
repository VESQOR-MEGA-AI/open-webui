<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { fade } from 'svelte/transition';
	import { toast } from 'svelte-sonner';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import { copyToClipboard } from '$lib/utils';

	import {
		cardPhase,
		describeFailure,
		elapsedSeconds,
		isRetryDisabled,
		PROVIDER_LABELS,
		SLOW_GENERATION_AFTER_SECONDS,
		type CardState
	} from './state';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let card: CardState;
	export let onRetry: () => void;

	/** This card's own ticker. Three cards, three intervals, each cleaning up after itself. */
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
		// Settled: stop counting immediately rather than waiting for destroy.
		stopTicker();
	}

	onDestroy(stopTicker);

	$: phase = cardPhase(card);
	$: elapsed = elapsedSeconds(card, now);
	$: failureCopy = card.failure ? describeFailure(card.failure) : null;
	$: retryDisabled = isRetryDisabled(card);
	$: retryLabel = card.answer ? $i18n.t('Regenerate') : $i18n.t('Retry');

	// Every disabled control states its real reason: the banner beside it already
	// says why, and a tooltip that says something else contradicts it.
	$: retryDisabledReason = !retryDisabled
		? $i18n.t('Generating again keeps the current answer as an earlier version.')
		: card.inFlight
			? $i18n.t('Generation in progress.')
			: card.failure
				? $i18n.t(describeFailure(card.failure).key, describeFailure(card.failure).params)
				: $i18n.t('Generation in progress.');

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
		// Listen rather than read once: the setting can change while the page is open.
		motionQuery.addEventListener('change', readMotionPreference);
	});

	onDestroy(() => {
		motionQuery?.removeEventListener('change', readMotionPreference);
	});

	// 150ms explains that content replaced the spinner; nothing else animates.
	$: fadeParams = reducedMotion ? { duration: 0 } : { duration: 150 };

	const copy = async () => {
		if (!card.answer?.text) return;
		await copyToClipboard(card.answer.text);
		toast.success($i18n.t('Copying to clipboard was successful!'));
	};
</script>

<div
	class="flex flex-col min-w-0 rounded-xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900"
>
	<div
		class="flex items-center justify-between gap-2 px-3.5 py-2.5 border-b border-gray-100 dark:border-gray-850"
	>
		<div class="text-sm font-medium truncate">{PROVIDER_LABELS[card.provider]}</div>

		<div class="flex items-center gap-1.5 shrink-0">
			{#if card.answer?.text}
				<Tooltip content={$i18n.t('Copy')}>
					<button
						class="px-2 py-1 text-xs rounded-lg bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={copy}
					>
						{$i18n.t('Copy')}
					</button>
				</Tooltip>
			{/if}

			{#if phase !== 'requires_configuration'}
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
			<!--
				Progress is a strip ABOVE the answer, never a spinner over it: an answer
				already rendered is removed by nothing except a successful new version.
			-->
			{#if card.inFlight}
				<div
					class="flex items-center gap-2 text-xs text-gray-500 {card.answer
						? 'pb-2 border-b border-gray-100 dark:border-gray-850'
						: ''}"
				>
					<Spinner className="size-3.5" />
					<span>{card.answer ? $i18n.t('Regenerating…') : $i18n.t('Generating…')}</span>
					<span class="tabular-nums">{elapsed}s</span>
				</div>
				{#if elapsed >= SLOW_GENERATION_AFTER_SECONDS}
					<div class="text-xs text-gray-500">
						{$i18n.t('Reasoning models can take a few minutes.')}
					</div>
				{/if}
			{/if}

			{#if failureCopy}
				<div
					class="text-xs rounded-lg px-2.5 py-2 bg-red-500/10 text-red-600 dark:text-red-400 break-words"
				>
					<div>{$i18n.t(failureCopy.key, failureCopy.params)}</div>
					{#if card.failure?.code === 'oversized'}
						<div class="mt-1 text-gray-500">
							{$i18n.t("This is the limit configured for this page, not the provider's context window.")}
						</div>
					{/if}
				</div>
			{/if}

			{#if card.answer?.text}
				<div class="answer-body min-w-0 text-sm" in:fade={fadeParams}>
					<Markdown id={card.answer.id} content={card.answer.text} done={true} />
				</div>
			{:else if !card.inFlight && !failureCopy}
				<div class="text-sm text-gray-500">{$i18n.t('No answer yet.')}</div>
			{/if}
		{/if}
	</div>

	{#if card.answer}
		<div
			class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2 text-xs text-gray-500 border-t border-gray-100 dark:border-gray-850"
		>
			{#if card.answer.model}
				<span class="truncate">{card.answer.model}</span>
			{/if}
			<!-- engine_version is omitted entirely when the provider reported none. -->
			{#if card.answer.engine_version}
				<span class="truncate">{card.answer.engine_version}</span>
			{/if}
			<span>{$i18n.t('Version {{revision}}', { revision: card.answer.revision })}</span>
		</div>
	{/if}
</div>

<style>
	/* Large answers scroll inside the card instead of growing the page. */
	.answer-body {
		max-height: 32rem;
		overflow-y: auto;
	}

	/* Tables are the one thing allowed to be wider than the card. */
	.answer-body :global(table) {
		display: block;
		max-width: 100%;
		overflow-x: auto;
	}
</style>
