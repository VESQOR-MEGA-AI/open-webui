<script lang="ts">
	import { getContext } from 'svelte';

	import { models } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Search from '$lib/components/icons/Search.svelte';

	const i18n = getContext('i18n');

	export let model_id = '';

	export let side: 'top' | 'bottom' = 'top';
	export let align: 'start' | 'end' = 'start';

	/** Optional callback when selection changes */
	export let onChange: () => void = () => {};

	let showDropdown = false;
	let modelSearch = '';
	// VESQOR: two-level menu. Root = DEFAULT (Lizz) + "Effort" (submenu with tiers).
	let view: 'root' | 'effort' = 'root';

	// Base models that back a custom model (e.g. vesqor-reasoning behind Lizz,
	// vesqor-pro behind os-pro) are hidden — same rule as the chat selector.
	$: baseModelIds = new Set(
		$models
			.map((model) => model?.info?.base_model_id)
			.filter((id): id is string => typeof id === 'string' && id.length > 0)
	);

	const TIER_ORDER = ['APEX', 'TITAN', 'ULTRA', 'PRO', 'PRIME', 'CORE', 'LIGHT'];

	$: visibleModels = $models
		.filter(
			(m) =>
				!baseModelIds.has(m.id) &&
				!(m?.info?.meta?.hidden ?? false)
		)
		.map((m) => ({
			...m,
			effortTier: m?.info?.meta?.effortTier ?? null,
			effortDesc: m?.info?.meta?.effortDesc ?? ''
		}))
		.sort((a, b) => {
			const ta = a.effortTier;
			const tb = b.effortTier;
			if (!ta && !tb) return a.name.localeCompare(b.name);
			if (!ta) return -1; // DEFAULT (Lizz) first
			if (!tb) return 1;
			return TIER_ORDER.indexOf(ta) - TIER_ORDER.indexOf(tb) || ta.localeCompare(tb);
		});

	$: defaultModel = visibleModels.find((m) => !m.effortTier) ?? null;
	$: effortModels = visibleModels.filter((m) => m.effortTier);

	$: filteredEffort = modelSearch
		? effortModels.filter(
				(m) =>
					m.name.toLowerCase().includes(modelSearch.toLowerCase()) ||
					m.id.toLowerCase().includes(modelSearch.toLowerCase()) ||
					(m.effortTier ?? '').toLowerCase().includes(modelSearch.toLowerCase())
			)
		: effortModels;

	$: modelLabel = model_id
		? (() => {
				const m = visibleModels.find((x) => x.id === model_id);
				if (!m) return model_id;
				return m.effortTier
					? `${(m.name || m.id).split(' (')[0]} · ${m.effortTier}`
					: m.name;
			})()
		: $i18n.t('Select model');

	function select(id: string) {
		model_id = id;
		showDropdown = false;
		view = 'root';
		modelSearch = '';
		onChange();
	}
</script>

<Dropdown bind:show={showDropdown} {side} {align}>
	<button
		type="button"
		class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-2xl text-xs transition
			text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
	>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 24 24"
			stroke-width="1.5"
			stroke="currentColor"
			class="size-3.5 shrink-0"
		>
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"
			/>
		</svg>
		<span class="whitespace-nowrap max-w-32 truncate">{modelLabel}</span>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 24 24"
			stroke-width="2"
			stroke="currentColor"
			class="size-2.5"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
		</svg>
	</button>

	<div
		slot="content"
		class="rounded-xl shadow-lg border border-gray-200 dark:border-gray-800 flex flex-col bg-white dark:bg-gray-850 w-72 p-0.5"
	>
		{#if view === 'root'}
			<div class="flex items-center gap-1.5 px-2 py-1">
				<Search className="size-3.5" strokeWidth="2.5" />
				<input
					bind:value={modelSearch}
					class="w-full text-[13px] bg-transparent outline-hidden"
					placeholder={$i18n.t('Search a model')}
					autocomplete="off"
					on:click={(e) => e.stopPropagation()}
				/>
			</div>

			<div class="overflow-y-auto scrollbar-thin max-h-60">
				{#if defaultModel}
					<button
						class="h-[1.6875rem] px-2 rounded-xl w-full text-left text-[13px] {model_id === defaultModel.id
							? 'text-gray-900 dark:text-gray-100'
							: 'text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100'}"
						type="button"
						on:click={() => select(defaultModel.id)}
					>
						<div class="flex items-center text-black dark:text-gray-100 line-clamp-1">
							<span class="font-semibold uppercase tracking-wide mr-1.5">{$i18n.t('DEFAULT')}</span>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-3.5 shrink-0 text-gray-400"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"
								/>
							</svg>
						</div>
						<div class="truncate pl-0.5 text-gray-500 dark:text-gray-400">{defaultModel.name}</div>
					</button>
				{/if}

				{#if effortModels.length > 0}
					<button
						class="h-[1.6875rem] px-2 rounded-xl w-full text-left text-[13px] text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100"
						type="button"
						on:click={() => {
							view = 'effort';
							modelSearch = '';
						}}
					>
						<div class="flex items-center justify-between text-black dark:text-gray-100">
							<span class="font-semibold uppercase tracking-wide">{$i18n.t('Effort')}</span>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="2"
								stroke="currentColor"
								class="size-3.5 shrink-0 text-gray-400"
							>
								<path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
							</svg>
						</div>
					</button>
				{/if}
			</div>
		{:else}
			<div class="flex items-center gap-1.5 px-1 py-1">
				<button
					type="button"
					class="flex items-center gap-1 rounded-lg px-1.5 py-1 text-[13px] text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
					on:click={() => {
						view = 'root';
						modelSearch = '';
					}}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-3.5"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
					</svg>
					<span class="font-semibold">{$i18n.t('Effort')}</span>
				</button>
				<div class="flex items-center gap-1.5 px-1 flex-1">
					<Search className="size-3.5" strokeWidth="2.5" />
					<input
						bind:value={modelSearch}
						class="w-full text-[13px] bg-transparent outline-hidden"
						placeholder={$i18n.t('Search a model')}
						autocomplete="off"
						on:click={(e) => e.stopPropagation()}
					/>
				</div>
			</div>

			<div class="overflow-y-auto scrollbar-thin max-h-60">
				{#each filteredEffort as model (model.id)}
					<button
						class="h-[1.6875rem] px-2 rounded-xl w-full text-left text-[13px] {model_id === model.id
							? 'text-gray-900 dark:text-gray-100'
							: 'text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100'}"
						type="button"
						on:click={() => select(model.id)}
					>
						<div class="flex items-center text-black dark:text-gray-100 line-clamp-1">
							<img
								src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${encodeURIComponent(model.id)}`}
								alt={model?.name ?? model.id}
								class="rounded-full size-5 items-center mr-2 shrink-0"
								loading="lazy"
								on:error={(e) => {
									e.currentTarget.src = '/favicon.png';
								}}
							/>
							<div class="truncate">
								<span class="font-semibold">{model.effortTier}</span>
								{#if model.effortDesc}
									<span class="ml-1 text-gray-400 dark:text-gray-500 truncate">
										{model.effortDesc}
									</span>
								{/if}
							</div>
						</div>
					</button>
				{:else}
					<div class="block px-2 py-1.5 text-[13px] text-gray-700 dark:text-gray-100">
						{$i18n.t('No results found')}
					</div>
				{/each}
			</div>
		{/if}
	</div>
</Dropdown>
