<script lang="ts">
	import { getContext } from 'svelte';

	import { selectedPersona } from '$lib/stores';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UserCircle from '$lib/components/icons/UserCircle.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	const i18n = getContext('i18n');

	export let show = false;

	// PERSONA-1 (2026-08-22): the five VESQOR report registers. null = auto
	// (the brain classifies the audience from the request text).
	const PERSONAS = [
		{ id: 'executive', label: 'Executive / CEO' },
		{ id: 'manager', label: 'Manager / Team Lead' },
		{ id: 'technical', label: 'Technical Lead' },
		{ id: 'developer', label: 'Developer' },
		{ id: 'general', label: 'End-User' }
	] as const;

	$: selectedLabel = PERSONAS.find((p) => p.id === $selectedPersona)?.label ?? null;

	const select = (id: string | null) => {
		selectedPersona.set(id);
		show = false;
	};
</script>

<div class="flex items-center translate-x-0.5">
	<Dropdown bind:show align="start">
		<Tooltip content="Report audience" placement="top">
			<button
				type="button"
				class="flex items-center gap-1.5 translate-y-[1px] text-sm text-gray-600 hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-300 dark:hover:bg-gray-800/40 dark:hover:text-gray-200 transition rounded-lg cursor-pointer p-1 {$selectedPersona ? '' : 'opacity-50'}"
			>
				<UserCircle className="size-3.5" strokeWidth="2" />

				{#if $selectedPersona && selectedLabel}
					<span class="truncate text-sm max-w-[100px] sm:max-w-[150px]">{selectedLabel}</span>
				{/if}
			</button>
		</Tooltip>

		<div slot="content">
			<DropdownMenu className="min-w-56 max-w-56 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
				<div class="flex items-center justify-between px-3 py-1">
					<span class="text-[10px] font-normal text-gray-400 dark:text-gray-500 uppercase tracking-wider">
						{$i18n.t('Report audience')}
					</span>
				</div>

				<button type="button" class="w-full" on:click={() => select(null)}>
					<span class="flex-1 text-left">{$i18n.t('Auto (detect from request)')}</span>
					{#if !$selectedPersona}
						<Check className="size-3.5" />
					{/if}
				</button>

				<hr />

				{#each PERSONAS as persona}
					<button type="button" class="w-full" on:click={() => select(persona.id)}>
						<span class="flex-1 text-left">{persona.label}</span>
						{#if $selectedPersona === persona.id}
							<Check className="size-3.5" />
						{/if}
					</button>
				{/each}
			</DropdownMenu>
		</div>
	</Dropdown>
</div>
