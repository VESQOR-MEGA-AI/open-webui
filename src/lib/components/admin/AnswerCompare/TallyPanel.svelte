<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { Tally } from '$lib/apis/answer-compare';
	import { tallyView } from './tallyState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	/**
	 * Renders the server's tally. Nothing here counts: every number on screen is
	 * the number the server sent, turned into copy by tallyState.
	 */
	export let tally: Tally;

	$: view = tallyView(tally);
</script>

<div
	class="flex flex-col gap-3 rounded-xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900 px-3.5 py-3 text-sm"
>
	<div class="text-base font-medium">{$i18n.t(view.headline.key, view.headline.params)}</div>

	<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-400">
		{#each view.votes as line (line.provider)}
			<span>{line.name}: {line.votes}</span>
		{/each}
	</div>

	{#if view.partial}
		<div class="text-xs text-amber-700 dark:text-amber-400">
			{$i18n.t(view.partial.key, view.partial.params)}
		</div>
	{/if}

	{#if view.excluded.length > 0}
		<ul class="text-xs text-gray-500 flex flex-col gap-0.5">
			{#each view.excluded as line (line.judge)}
				<li>
					{line.name} — {$i18n.t(line.reason.key, line.reason.params)}{#if line.latestAttempt}; {$i18n.t(
							line.latestAttempt.key,
							line.latestAttempt.params
						)}{/if}
					{#if line.selfVote}
						<span class="italic"> ({$i18n.t(line.selfVote.key, line.selfVote.params)})</span>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if view.verdicts.some((v) => v.text || v.selfVote)}
		<ul class="text-xs flex flex-col gap-0.5">
			{#each view.verdicts as line (line.judge)}
				{#if line.text || line.selfVote}
					<li>
						{#if line.text}<span>{$i18n.t(line.text.key, line.text.params)}</span>{/if}
						{#if line.selfVote}
							<!-- An annotation beside the verdict, never a change to the count. -->
							<span class="italic text-gray-500"> {$i18n.t(line.selfVote.key, line.selfVote.params)}</span>
						{/if}
					</li>
				{/if}
			{/each}
		</ul>
	{/if}

	<div class="text-xs text-gray-500 border-t border-gray-100 dark:border-gray-850 pt-2">
		{$i18n.t(view.footer.key, view.footer.params)}
	</div>
</div>
