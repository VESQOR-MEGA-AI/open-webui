<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { MappedAdjudication, ProviderId } from '$lib/apis/answer-compare';
	import {
		categoryRows,
		claimRows,
		confidenceWords,
		providerName,
		scoreRows,
		verdictLine
	} from './adjudicationState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	/**
	 * The scored view of one judge's adjudication. Every figure shown here was
	 * computed on the server from the judge's findings — none is a total the
	 * model wrote — so the page never has to decide who won.
	 */
	export let adjudication: MappedAdjudication;

	$: verdict = verdictLine(adjudication);
	$: scores = scoreRows(adjudication);
	$: categories = categoryRows(adjudication);
	$: claims = claimRows(adjudication);

	const percent = (value: number | null) => (value === null ? '—' : `${Math.round(value * 100)}%`);
</script>

<div class="flex flex-col gap-3">
	<!-- Verdict, margin and confidence -->
	<div class="flex flex-col gap-1">
		<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
			<span class="font-medium">
				{#if verdict.kind === 'winner'}
					{$i18n.t('Winner: {{provider}}', { provider: providerName(verdict.providers[0]) })}
				{:else if verdict.kind === 'tie'}
					{$i18n.t('Tie: {{providers}}', {
						providers: verdict.providers.map(providerName).join(', ')
					})}
				{:else}
					{$i18n.t('No reliable winner')}
				{/if}
			</span>

			{#if verdict.kind === 'winner'}
				<span class="text-xs text-gray-500">
					{$i18n.t('by {{margin}} points', { margin: adjudication.winning_margin.toFixed(1) })}
				</span>
			{/if}

			<Tooltip content={adjudication.confidence_reasons.join(' ')} interactive={true}>
				<span
					class="text-xs px-1.5 py-0.5 rounded-full {adjudication.confidence === 'high'
						? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
						: adjudication.confidence === 'medium'
							? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
							: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'}"
				>
					{$i18n.t('{{level}} confidence', { level: confidenceWords(adjudication.confidence) })}
				</span>
			</Tooltip>
		</div>

		{#if adjudication.tie_break_used}
			<div class="text-xs text-gray-500">
				{$i18n.t('Scores were within one point; decided on {{dimension}}.', {
					dimension: adjudication.tie_break_used.replace(/_/g, ' ')
				})}
			</div>
		{/if}

		{#if !adjudication.evidence_complete}
			<!-- Never let a partial corpus read as a complete adjudication. -->
			<div
				class="text-xs rounded-md px-2 py-1 bg-amber-50 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
			>
				{$i18n.t('Adjudicated on incomplete evidence.')}
				{#if adjudication.dropped_evidence_ids.length > 0}
					{$i18n.t('{{count}} source item(s) were dropped for length.', {
						count: adjudication.dropped_evidence_ids.length
					})}
				{/if}
				{#if adjudication.truncated_providers.length > 0}
					{$i18n.t('Truncated: {{providers}}.', {
						providers: adjudication.truncated_providers.map(providerName).join(', ')
					})}
				{/if}
			</div>
		{/if}

		{#if adjudication.injection_signals.length > 0}
			<div
				class="text-xs rounded-md px-2 py-1 bg-red-50 text-red-900 dark:bg-red-900/30 dark:text-red-200"
			>
				{$i18n.t(
					'{{count}} passage(s) tried to instruct the adjudicator. They were assessed as content and changed nothing.',
					{ count: adjudication.injection_signals.length }
				)}
			</div>
		{/if}
	</div>

	<!-- Final scores -->
	<div>
		<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
			{$i18n.t('Final scores')}
		</div>
		<ul class="mt-1 flex flex-col gap-1">
			{#each scores as row (row.provider)}
				<li class="flex items-center gap-2 text-sm">
					<span class="w-24 shrink-0 truncate">{providerName(row.provider)}</span>
					<div class="flex-1 h-1.5 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
						<div
							class="h-full rounded-full {row.winner
								? 'bg-green-500'
								: 'bg-gray-400 dark:bg-gray-600'}"
							style="width: {row.score}%"
						></div>
					</div>
					<span class="w-12 shrink-0 text-right tabular-nums">{row.score.toFixed(1)}</span>
					{#if row.integrityCapped}
						<Tooltip
							content={$i18n.t(
								'Capped: a severe fabrication or factual error means coverage cannot compensate.'
							)}
						>
							<span class="text-xs text-red-600 dark:text-red-400">{$i18n.t('capped')}</span>
						</Tooltip>
					{/if}
				</li>
			{/each}
		</ul>
	</div>

	<!-- Category-by-category -->
	<Collapsible
		buttonClassName="w-full py-1 text-xs font-medium text-left text-gray-500 uppercase tracking-wide hover:text-gray-700 dark:hover:text-gray-300 transition"
		chevron={true}
	>
		<div>{$i18n.t('Category scores')}</div>
		<div slot="content" class="pb-1 overflow-x-auto">
			<table class="w-full text-xs">
				<thead>
					<tr class="text-gray-500">
						<th class="text-left font-medium py-1 pr-2">{$i18n.t('Category')}</th>
						<th class="text-right font-medium py-1 px-1">{$i18n.t('Max')}</th>
						{#each scores as row (row.provider)}
							<th class="text-right font-medium py-1 px-1">{providerName(row.provider)}</th>
						{/each}
						<th class="text-left font-medium py-1 pl-2">{$i18n.t('Best')}</th>
					</tr>
				</thead>
				<tbody>
					{#each categories as row (row.key)}
						<tr class="border-t border-gray-100 dark:border-gray-800">
							<td class="py-1 pr-2">{$i18n.t(row.title)}</td>
							<td class="py-1 px-1 text-right tabular-nums text-gray-500">{row.weight}</td>
							{#each row.cells as cell (cell.provider)}
								<td
									class="py-1 px-1 text-right tabular-nums {cell.best
										? 'font-medium text-green-700 dark:text-green-400'
										: ''}"
								>
									{cell.score.toFixed(1)}
								</td>
							{/each}
							<td class="py-1 pl-2 text-gray-500">{row.winners.map(providerName).join(', ')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Collapsible>

	<!-- Claim validation: what was checked against evidence, as opposed to judged -->
	<Collapsible
		buttonClassName="w-full py-1 text-xs font-medium text-left text-gray-500 uppercase tracking-wide hover:text-gray-700 dark:hover:text-gray-300 transition"
		chevron={true}
	>
		<div>{$i18n.t('Claim validation')}</div>
		<div slot="content" class="flex flex-col gap-2 pb-1">
			<div class="text-xs text-gray-500">
				{$i18n.t(
					'Verified means supported by the supplied source material. Unsupported and not-verifiable claims have not been proven.'
				)}
			</div>
			{#each claims as row (row.provider)}
				<div class="text-xs">
					<div class="font-medium">{providerName(row.provider)}</div>
					<ul class="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-gray-600 dark:text-gray-400">
						{#each row.counts as count (count.classification)}
							<li>
								<span class="tabular-nums">{count.value}</span>
								{$i18n.t(count.title)}
							</li>
						{/each}
					</ul>
					<div class="mt-0.5 text-gray-500">
						{$i18n.t('Accuracy ratio')}: {percent(row.accuracyRatio)} ·
						{$i18n.t('Critical coverage')}: {percent(row.criticalCoverage)}
						{#if row.penaltyTotal > 0}
							· {$i18n.t('Penalties')}: −{row.penaltyTotal.toFixed(1)}
						{/if}
					</div>
				</div>
			{/each}
		</div>
	</Collapsible>

	<!-- Head-to-head -->
	{#if adjudication.pairwise_results.length > 0}
		<Collapsible
			buttonClassName="w-full py-1 text-xs font-medium text-left text-gray-500 uppercase tracking-wide hover:text-gray-700 dark:hover:text-gray-300 transition"
			chevron={true}
		>
			<div>{$i18n.t('Head-to-head')}</div>
			<div slot="content" class="flex flex-col gap-1.5 pb-1 text-xs">
				{#each adjudication.pairwise_results as pair}
					<div>
						<div>
							<span class="font-medium">
								{providerName(pair.first)} vs {providerName(pair.second)}
							</span>
							<span class="text-gray-500">
								—
								{#if pair.stronger === 'tie'}
									{$i18n.t('level')}
								{:else}
									{$i18n.t('{{provider}} by {{margin}}', {
										provider: providerName(pair.stronger),
										margin: pair.margin.toFixed(1)
									})}
								{/if}
							</span>
						</div>
						{#if pair.reason}
							<div class="text-gray-600 dark:text-gray-400 report-text">{pair.reason}</div>
						{/if}
						{#if !pair.agrees_with_scores}
							<div class="text-amber-700 dark:text-amber-400">
								{$i18n.t("The judge's wording disagreed with its own scores here.")}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</Collapsible>
	{/if}

	<!-- Why, and what the loser would have to do -->
	{#if adjudication.decisive_reasons.length > 0}
		<div>
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
				{$i18n.t('Decisive reasons')}
			</div>
			<ul class="mt-1 list-disc pl-5 flex flex-col gap-0.5 text-sm">
				{#each adjudication.decisive_reasons as reason}
					<li class="report-text">{reason}</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if adjudication.winner_gap_analysis.length > 0}
		<div>
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
				{$i18n.t('Where the winner is still weak')}
			</div>
			<ul class="mt-1 list-disc pl-5 flex flex-col gap-0.5 text-sm">
				{#each adjudication.winner_gap_analysis as gap}
					<li class="report-text">{gap}</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#each Object.entries(adjudication.loser_recovery_analysis) as [provider, actions] (provider)}
		{#if actions && actions.length > 0}
			<div>
				<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
					{$i18n.t('How {{provider}} would close the gap', {
						provider: providerName(provider as ProviderId)
					})}
				</div>
				<ul class="mt-1 list-disc pl-5 flex flex-col gap-0.5 text-sm">
					{#each actions as action}
						<li class="report-text">{action}</li>
					{/each}
				</ul>
			</div>
		{/if}
	{/each}

	{#if adjudication.unresolved_uncertainty.length > 0}
		<div>
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wide">
				{$i18n.t('Unresolved uncertainty')}
			</div>
			<ul class="mt-1 list-disc pl-5 flex flex-col gap-0.5 text-sm">
				{#each adjudication.unresolved_uncertainty as item}
					<li class="report-text">{item}</li>
				{/each}
			</ul>
		</div>
	{/if}

	<div class="text-[10px] text-gray-400">
		{$i18n.t('Adjudication engine {{engine}} · rubric {{rubric}}', {
			engine: adjudication.engine_version,
			rubric: adjudication.rubric_version
		})}
	</div>
</div>

<style>
	.report-text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
