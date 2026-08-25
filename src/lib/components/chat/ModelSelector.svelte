<script lang="ts">
	import { models, settings, user } from '$lib/stores';
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Selector from './ModelSelector/Selector.svelte';

	import { updateUserSettings } from '$lib/apis/users';
	import equal from 'fast-deep-equal';
	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	export let showSetDefault = true;
	export let triggerClassName = 'text-lg';
	export let className = undefined;
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';

	let compareModels = selectedModels.length > 1;

	const saveDefaultModel = async () => {
		const hasEmptyModel = selectedModels.filter((it) => it === '');
		if (hasEmptyModel.length) {
			toast.error($i18n.t('Choose a model before saving...'));
			return;
		}
		settings.set({ ...$settings, models: selectedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });

		toast.success($i18n.t('Default model updated'));
	};

	const pinModelHandler = async (modelId) => {
		let pinnedModels = $settings?.pinnedModels ?? [];

		if (pinnedModels.includes(modelId)) {
			pinnedModels = pinnedModels.filter((id) => id !== modelId);
		} else {
			pinnedModels = [...new Set([...pinnedModels, modelId])];
		}

		settings.set({ ...$settings, pinnedModels: pinnedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	$: if (selectedModels.length > 0 && $models.length > 0) {
		const _selectedModels = selectedModels.map((model) =>
			$models.map((m) => m.id).includes(model) ? model : ''
		);

		if (!equal(_selectedModels, selectedModels)) {
			selectedModels = _selectedModels;
		}
	}

	$: if (selectedModels.length > 1 && !compareModels) {
		compareModels = true;
	}

	// VESQOR: hide base models that back a custom model (e.g. vesqor-reasoning
	// backs "Lizz") — the custom model is the only surface users see.
	// NOTE: the API nests base_model_id under model.info (not top-level).
	$: baseModelIds = new Set(
		$models
			.map((model) => model.info?.base_model_id)
			.filter((id): id is string => typeof id === 'string' && id.length > 0)
	);
	$: visibleModels = $models.filter((model) => !baseModelIds.has(model.id));

	// VESQOR: effort tiers ordered by task complexity — simple → hard.
	const TIER_ORDER = ['LIGHT', 'CORE', 'PRIME', 'PRO', 'ULTRA', 'TITAN', 'APEX'];

	$: sortedModels = [...visibleModels].sort((a, b) => {
		const ta = a.info?.meta?.effortTier;
		const tb = b.info?.meta?.effortTier;
		if (!ta && !tb) return a.name.localeCompare(b.name);
		if (!ta) return -1; // DEFAULT (Lizz) first
		if (!tb) return 1;
		return TIER_ORDER.indexOf(ta) - TIER_ORDER.indexOf(tb) || ta.localeCompare(tb);
	});
	</script>

	<div class="flex min-w-0 max-w-full flex-col items-start">
		<div class="flex min-w-0 max-w-full">
			<div class="min-w-0 max-w-full overflow-hidden">
				<div class="min-w-0 max-w-full">
					<Selector
						id="model"
						placeholder={$i18n.t('Select a model')}
						items={sortedModels.map((model) => ({
							value: model.id,
							label: model.info?.meta?.effortTier
								? `Lizz 9.2 (${model.info.meta.effortTier})`
								: model.name,
							model: model,
							effortTier: model.info?.meta?.effortTier,
							effortDesc: model.info?.meta?.effortDesc
						}))}
					{pinModelHandler}
					{className}
					{triggerClassName}
					{placement}
					{align}
					{showSetDefault}
					onSetDefault={saveDefaultModel}
					vesqorTierMenu
					multipleEnabled={$user?.role === 'admin' ||
						($user?.permissions?.chat?.multiple_models ?? true)}
					{disabled}
					bind:compareEnabled={compareModels}
					bind:values={selectedModels}
				/>
			</div>
		</div>
	</div>
</div>
