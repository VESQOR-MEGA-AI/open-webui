<script lang="ts">
	import { onMount } from 'svelte';

	import {
		getVesqorUserBrain,
		addVesqorBrainMemory,
		patchVesqorBrainMemory,
		deleteVesqorBrainMemory
	} from '$lib/apis/vesqor';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Trash from '$lib/components/icons/Trash.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import ChartBar from '$lib/components/icons/ChartBar.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import { toast } from 'svelte-sonner';

	const LAYERS = [
		{ id: 'episodic', label: 'Reports & Questions', icon: ChartBar },
		{ id: 'semantic', label: 'Facts & Knowledge', icon: Database },
		{ id: 'preference', label: 'Preferences', icon: Check },
		{ id: 'decision', label: 'Decisions', icon: Check },
		{ id: 'document', label: 'Documents', icon: Document },
		{ id: 'working', label: 'Working Notes', icon: Document },
		{ id: 'entity', label: 'Entities', icon: Database }
	];

	let loading = true;
	let error: string | null = null;
	let data: any = null;
	let activeLayer = 'episodic';
	let newContent = '';
	let adding = false;

	const load = async () => {
		loading = true;
		error = null;
		try {
			data = await getVesqorUserBrain(localStorage.token, activeLayer);
		} catch (err: any) {
			error = typeof err === 'string' ? err : (err?.detail ?? 'Failed to load your Brain');
		} finally {
			loading = false;
		}
	};

	onMount(load);

	const switchLayer = async (layer: string) => {
		activeLayer = layer;
		await load();
	};

	const addMemory = async () => {
		if (!newContent.trim()) return;
		adding = true;
		try {
			await addVesqorBrainMemory(localStorage.token, {
				layer: activeLayer,
				content: newContent.trim(),
				source: 'user'
			});
			newContent = '';
			toast.success('Saved to your Brain');
			await load();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : 'Failed to save');
		} finally {
			adding = false;
		}
	};

	const togglePin = async (id: string, pinned: boolean) => {
		try {
			await patchVesqorBrainMemory(localStorage.token, id, { pinned: !pinned });
			await load();
		} catch (err: any) {
			toast.error('Failed to update');
		}
	};

	const forget = async (id: string) => {
		try {
			await deleteVesqorBrainMemory(localStorage.token, id);
			toast.success('Forgotten');
			await load();
		} catch (err: any) {
			toast.error('Failed to forget');
		}
	};

	$: memories = data?.memories ?? [];
	$: entities = data?.entities ?? [];
	$: relations = data?.relations ?? [];
</script>

<div class="w-full h-full flex flex-col overflow-y-auto">
	{#if loading}
		<div class="flex justify-center py-8">
			<Spinner />
		</div>
	{:else if error}
		<div class="text-xs text-red-500 py-4">{error}</div>
	{:else}
		<div class="p-3 mb-3 rounded-lg bg-violet-50 dark:bg-violet-950/40 border border-violet-200 dark:border-violet-800 text-xs leading-relaxed">
			<span class="font-semibold text-violet-700 dark:text-violet-300">Your Brain.</span>
			<span class="text-violet-800 dark:text-violet-200">
				Private memory that only you can see. VESQOR learns your companies, projects, decisions and
				preferences — and uses them to answer better. You can edit, pin or forget anything.
			</span>
		</div>

		<!-- Layer tabs -->
		<div class="flex flex-wrap gap-1.5 mb-3">
			{#each LAYERS as layer}
				<button
					class="px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors {activeLayer ===
					layer.id
						? 'bg-violet-600 text-white'
						: 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700'}"
					onclick={() => switchLayer(layer.id)}
				>
					{layer.label}
				</button>
			{/each}
		</div>

		<!-- Add memory -->
		<div class="flex gap-2 mb-3">
			<input
				class="flex-1 px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-xs outline-none focus:ring-2 focus:ring-violet-500"
				placeholder="Add a note to your Brain…"
				bind:value={newContent}
				onkeydown={(e) => e.key === 'Enter' && addMemory()}
			/>
			<button
				class="px-3 py-2 rounded-lg bg-violet-600 text-white text-xs font-medium hover:bg-violet-700 disabled:opacity-50"
				disabled={adding || !newContent.trim()}
				onclick={addMemory}
			>
				<Plus class="w-3.5 h-3.5" />
			</button>
		</div>

		<!-- Memories -->
		<UserSettingSection title={LAYERS.find((l) => l.id === activeLayer)?.label ?? 'Memory'} first>
			{#if memories.length === 0}
				<div class="text-xs text-gray-400 dark:text-gray-500 py-3 text-center">
					Nothing here yet. Ask VESQOR a question and it will learn from the answer.
				</div>
			{:else}
				{#each memories as memory}
					<div
						class="flex items-start gap-2 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/60 border border-gray-100 dark:border-gray-800"
					>
						<div class="flex-1 min-w-0">
							<div class="text-xs text-gray-800 dark:text-gray-200 line-clamp-3">{memory.content}</div>
							<div class="mt-1 flex items-center gap-2 text-[0.625rem] text-gray-400 dark:text-gray-500">
								<span>{memory.source}</span>
								{#if memory.confidence}<span>· {memory.confidence}%</span>{/if}
								{#if memory.pinned}<span class="text-violet-500">· pinned</span>{/if}
							</div>
						</div>
						<div class="flex items-center gap-1 shrink-0">
							<button
								class="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-violet-500"
								title="Pin"
								onclick={() => togglePin(memory.id, memory.pinned)}
							>
								<Pin class="w-3.5 h-3.5" />
							</button>
							<button
								class="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-red-500"
								title="Forget"
								onclick={() => forget(memory.id)}
							>
								<Trash class="w-3.5 h-3.5" />
							</button>
						</div>
					</div>
				{/each}
			{/if}
		</UserSettingSection>

		<!-- Entities -->
		{#if entities.length > 0}
			<UserSettingSection title="Entities">
				<div class="flex flex-wrap gap-1.5">
					{#each entities as entity}
						<span
							class="px-2 py-1 rounded-full text-[0.625rem] font-medium bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300"
						>
							{entity.type}: {entity.name}
						</span>
					{/each}
				</div>
			</UserSettingSection>
		{/if}
	{/if}
</div>
