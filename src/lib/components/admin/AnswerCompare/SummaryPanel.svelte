<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { copyToClipboard } from '$lib/utils';
	import type { SummaryRow } from '$lib/apis/answer-compare';
	import { summaryView } from './summaryState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	/**
	 * Renders the server-assembled narrative as plain text. Not Markdown and never
	 * {@html}: judge quotes are inside it, and the whole document is untrusted
	 * output. `white-space: pre-wrap` keeps the section breaks the server put there.
	 */
	export let summary: SummaryRow;

	$: view = summaryView(summary);

	const copy = async () => {
		await copyToClipboard(view.narrative);
		toast.success($i18n.t('Copying to clipboard was successful!'));
	};
</script>

<div
	class="flex flex-col gap-3 rounded-xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900 px-3.5 py-3"
>
	<div class="flex items-center justify-between gap-2">
		<div class="text-base font-medium">
			{$i18n.t('Summary')} · {$i18n.t(view.version.key, view.version.params)}
		</div>
		<button
			class="px-2 py-1 text-xs rounded-lg bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 transition"
			on:click={copy}
		>
			{$i18n.t('Copy')}
		</button>
	</div>

	{#if view.outdated}
		<div class="text-xs rounded-lg px-2.5 py-2 bg-amber-500/10 text-amber-700 dark:text-amber-400">
			{$i18n.t(view.outdated.key, view.outdated.params)}
		</div>
	{/if}

	<div class="summary-text text-sm">{view.narrative}</div>
</div>

<style>
	.summary-text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: 40rem;
		overflow-y: auto;
	}
</style>
