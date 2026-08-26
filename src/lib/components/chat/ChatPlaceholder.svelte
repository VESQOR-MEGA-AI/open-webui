<script lang="ts">
	import { config, user, models as _models, temporaryChatEnabled } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	import { fade } from 'svelte/transition';

	import Suggestions from './Suggestions.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EyeSlash from '$lib/components/icons/EyeSlash.svelte';

	const i18n = getContext('i18n');

	export let modelIds = [];
	export let models = [];
	export let atSelectedModel;

	export let onSelect = (e) => {};

	let mounted = false;
	let selectedModelIdx = 0;

	$: if (modelIds.length > 0) {
		selectedModelIdx = models.length - 1;
	}

	$: models = modelIds.map((id) => $_models.find((m) => m.id === id));

	onMount(() => {
		mounted = true;
	});
</script>

{#key mounted}
	<div class="m-auto w-full max-w-[58rem] px-8 lg:px-20">
		{#if $temporaryChatEnabled}
			<Tooltip
				content={$i18n.t("This chat won't appear in history and your messages will not be saved.")}
				className="w-full flex justify-start mb-0.5"
				placement="top"
			>
				<div class="flex items-center gap-1.5 text-gray-500 text-xs mt-1 w-fit">
					<EyeSlash strokeWidth="2" className="size-3.5" />{$i18n.t('Temporary Chat')}
				</div>
			</Tooltip>
		{/if}

			<div class="mt-2 mb-4 text-3xl text-gray-800 dark:text-gray-100 text-left flex items-center gap-4">
				<div>
					<div class="line-clamp-1 font-semibold" in:fade={{ duration: 200 }}>
						VESQOR MEGA AI
					</div>

				<div in:fade={{ duration: 200, delay: 200 }}>
					<div class=" text-gray-400 dark:text-gray-500 line-clamp-1 font-p">
						{$i18n.t('How can I help you today?')}
					</div>
				</div>
			</div>
		</div>

		<div class=" w-full" in:fade={{ duration: 200, delay: 300 }}>
			<Suggestions
				className="grid grid-cols-2"
				suggestionPrompts={atSelectedModel?.info?.meta?.suggestion_prompts ??
					models[selectedModelIdx]?.info?.meta?.suggestion_prompts ??
					$config?.default_prompt_suggestions ??
					[]}
				{onSelect}
			/>
		</div>
	</div>
{/key}
