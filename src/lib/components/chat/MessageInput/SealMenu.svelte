<script lang="ts">
	import { getContext } from 'svelte';

	import { selectedSeal, sealConfirmed } from '$lib/stores';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Lock from '$lib/components/icons/Lock.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	const i18n = getContext('i18n');

	export let show = false;

	// SEAL-1 (2026-09-12): the confidentiality seal selector. STANDARD is the
	// default and is never sent to the brain; PRIVATE/CONFIDENTIAL are
	// forwarded as vq_seal for the backend to validate (Chat.svelte).
	const SEALS = [
		{ id: 'STANDARD', label: 'Standard' },
		{ id: 'PRIVATE', label: 'Private' },
		{ id: 'CONFIDENTIAL', label: 'Confidential' }
	] as const;

	$: selectedLabel = SEALS.find((s) => s.id === $selectedSeal)?.label ?? null;

	const select = (id: 'STANDARD' | 'PRIVATE' | 'CONFIDENTIAL') => {
		selectedSeal.set(id);
		show = false;
	};
</script>

<div class="flex items-center gap-1 translate-x-0.5">
	<Dropdown bind:show align="start">
		<Tooltip content="Confidentiality" placement="top">
			<button
				type="button"
				class="flex items-center gap-1.5 translate-y-[1px] text-sm text-gray-600 hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-300 dark:hover:bg-gray-800/40 dark:hover:text-gray-200 transition rounded-lg cursor-pointer p-1 {$selectedSeal === 'CONFIDENTIAL' ? 'text-red-600 dark:text-red-400' : $selectedSeal === 'STANDARD' ? 'opacity-50' : ''}"
			>
				<Lock className="size-3.5" strokeWidth="2" />

				{#if $selectedSeal !== 'STANDARD' && selectedLabel}
					<span class="truncate text-sm max-w-[100px] sm:max-w-[150px]">
						{$i18n.t(selectedLabel)}
					</span>
				{/if}
			</button>
		</Tooltip>

		<div slot="content">
			<DropdownMenu className="min-w-56 max-w-56 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
				<div class="flex items-center justify-between px-3 py-1">
					<span class="text-[10px] font-normal text-gray-400 dark:text-gray-500 uppercase tracking-wider">
						{$i18n.t('Confidentiality')}
					</span>
				</div>

				{#each SEALS as seal}
					<button type="button" class="w-full" on:click={() => select(seal.id)}>
						<span class="flex-1 text-left">{$i18n.t(seal.label)}</span>
						{#if $selectedSeal === seal.id}
							<Check className="size-3.5" />
						{/if}
					</button>
				{/each}
			</DropdownMenu>
		</div>
	</Dropdown>

	{#if $sealConfirmed === 'CONFIDENTIAL'}
		<Tooltip content="Confirmed by the brain" placement="top">
			<span
				class="flex items-center gap-1 text-xs text-red-600 dark:text-red-400 px-1 select-none"
			>
				<Lock className="size-3" strokeWidth="2" />
				{$i18n.t('Confidentiality confirmed')}
			</span>
		</Tooltip>
	{/if}
</div>
