<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import type { MappedReport, ProviderId } from '$lib/apis/answer-compare';
	import { answerSections, verdictHeadline, type NamedProvider } from './judgeState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	/**
	 * Renders the server-mapped report. Everything here is the judge's own text,
	 * untrusted, rendered as plain text with pre-wrap so newlines and lists a judge
	 * writes inside a note survive. The text is never rewritten: "(was B)" next to a
	 * provider name is what makes "Answer B is more accurate" readable.
	 */
	export let mapped: MappedReport;
	export let labelMap: Record<string, ProviderId> | null;

	$: headline = verdictHeadline(mapped, labelMap);
	$: sections = answerSections(mapped, labelMap);

	const named = (item: NamedProvider) =>
		item.label ? $i18n.t('{{name}} (was {{label}})', { name: item.name, label: item.label }) : item.name;
</script>

<div class="flex flex-col gap-3 text-sm">
	<div>
		<div class="font-medium">
			{#if headline.kind === 'winner'}
				{$i18n.t('Winner: {{providers}}', { providers: headline.providers.map(named).join(', ') })}
			{:else if headline.kind === 'tie'}
				{$i18n.t('Tie: {{providers}}', { providers: headline.providers.map(named).join(', ') })}
			{:else}
				{$i18n.t('No reliable winner')}
			{/if}
		</div>
		<div class="report-text mt-1 text-gray-700 dark:text-gray-300">{mapped.rationale}</div>
	</div>

	{#each sections as section (section.named.provider)}
		<!-- Header goes through the default slot: Collapsible types `title` as null. -->
		<Collapsible
			open={true}
			buttonClassName="w-full py-1 text-sm font-medium text-left hover:text-gray-700 dark:hover:text-gray-300 transition"
			chevron={true}
		>
			<div>{named(section.named)}</div>
			<div slot="content" class="flex flex-col gap-2 pb-1">
				{#if section.lists.length === 0}
					<div class="text-xs text-gray-500">{$i18n.t('No observations recorded.')}</div>
				{/if}
				{#each section.lists as list (list.field)}
					<div>
						<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">{$i18n.t(list.title)}</div>
						<ul class="mt-1 flex flex-col gap-1.5">
							{#each list.items as item}
								<li class="flex flex-col gap-0.5">
									{#if item.passage}
										<blockquote
											class="report-text border-l-2 border-gray-300 dark:border-gray-700 pl-2 text-gray-600 dark:text-gray-400 italic"
										>
											{item.passage}
										</blockquote>
									{/if}
									<div class="report-text">{item.note}</div>
								</li>
							{/each}
						</ul>
					</div>
				{/each}
			</div>
		</Collapsible>
	{/each}

	{#if mapped.needs_verification.length > 0}
		<div>
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
				{$i18n.t('Claims that need verification')}
			</div>
			<ul class="mt-1 list-disc pl-5 flex flex-col gap-0.5">
				{#each mapped.needs_verification as claim}
					<li class="report-text">{claim}</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>

<style>
	/* Plain text collapses newlines; a multi-line rationale or a list inside a
	   note would otherwise flatten into one paragraph. */
	.report-text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
