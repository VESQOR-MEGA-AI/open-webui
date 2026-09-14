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

			<div class="mt-2 mb-4 text-3xl text-gray-800 dark:text-gray-100 text-left">
				<!-- VESQOR orb-логотип: зелёный градиентный орб с мягким свечением —
				     виден на тёмном фоне чата. Без тёмной подложки (она сливалась
				     с фоном). aria-hidden, чисто декоративный. -->
				<div class="mb-5 text-gray-800 dark:text-gray-100 flex justify-center" aria-hidden="true">
					<svg viewBox="0 0 32 32" class="size-20 drop-shadow-[0_0_18px_rgba(57,181,74,0.45)]">
						<defs>
							<linearGradient id="vq25-orb-g" x1="0" y1="0" x2="1" y2="1">
								<stop offset="0" stop-color="#006838" />
								<stop offset="0.5" stop-color="#009444" />
								<stop offset="1" stop-color="#39b54a" />
							</linearGradient>
						</defs>
						<circle cx="16" cy="16" r="10" fill="url(#vq25-orb-g)" />
						<circle cx="16" cy="16" r="4" fill="#0b1f15" opacity="0.5" />
					</svg>
				</div>
				<div>
					<div class="line-clamp-1 font-semibold text-center" in:fade={{ duration: 200 }}>
						VESQOR MEGA AI
					</div>

					<div in:fade={{ duration: 200, delay: 200 }}>
						<div class=" text-gray-400 dark:text-gray-500 line-clamp-1 font-p text-center">
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
