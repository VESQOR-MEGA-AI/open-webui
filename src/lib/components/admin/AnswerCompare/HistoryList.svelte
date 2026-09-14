<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import dayjs from '$lib/dayjs';

	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import { hasOlder, historyRows, EMPTY_HISTORY_COPY, type HistoryState } from './historyState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	/** Rendered from the server's rows; nothing here counts or re-orders. */
	export let history: HistoryState;
	export let open = true;
	export let onOpenRun: (runId: string) => void;
	export let onRerun: (runId: string) => void;
	export let onLoadOlder: () => void;

	$: rows = historyRows(history);
</script>

<Collapsible bind:open chevron={true} buttonClassName="w-full py-1 text-base font-medium text-left">
	<div>{$i18n.t('History')}</div>

	<div slot="content" class="flex flex-col gap-2 pt-1">
		{#if history.loaded && rows.length === 0}
			<div class="text-sm text-gray-500">{$i18n.t(EMPTY_HISTORY_COPY)}</div>
		{/if}

		{#each rows as row (row.id)}
			<div
				class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-gray-100 dark:border-gray-850 px-3 py-2 text-sm min-w-0"
			>
				<span class="text-xs text-gray-500 shrink-0">{dayjs.unix(row.createdAt).format('D MMM HH:mm')}</span>
				<span class="truncate min-w-0 flex-1">{row.excerpt}{row.truncated ? '…' : ''}</span>

				<span class="flex flex-wrap items-center gap-1.5 text-xs text-gray-500 shrink-0">
					{#if row.isRerun}
						<span class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-850">{$i18n.t('rerun')}</span>
					{/if}
					{#each row.chips as chip}
						<span class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-850">{$i18n.t(chip.key, chip.params)}</span>
					{/each}
				</span>

				<span class="flex items-center gap-1.5 shrink-0">
					<button
						class="px-2 py-1 text-xs rounded-lg hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => onOpenRun(row.id)}
					>
						{$i18n.t('Open')}
					</button>
					<button
						class="px-2 py-1 text-xs rounded-lg hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => onRerun(row.id)}
					>
						{$i18n.t('Run again')}
					</button>
				</span>
			</div>
		{/each}

		{#if hasOlder(history)}
			<button
				class="w-fit px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
				on:click={onLoadOlder}
			>
				{$i18n.t('Load older')}
			</button>
		{/if}
	</div>
</Collapsible>
