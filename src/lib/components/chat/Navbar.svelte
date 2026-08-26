<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		WEBUI_NAME,
		banners,
		chatId,
		config,
		mobile,
		settings,
		showControls,
		showSidebar,
		temporaryChatEnabled,
		user
	} from '$lib/stores';

	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';

	import ShareChatModal from '../chat/ShareChatModal.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Menu from '$lib/components/layout/Navbar/Menu.svelte';
	import AdjustmentsHorizontal from '../icons/AdjustmentsHorizontal.svelte';

	import PencilSquare from '../icons/PencilSquare.svelte';
	import Banner from '../common/Banner.svelte';
	import Sidebar from '../icons/Sidebar.svelte';

	import ChatBubbleDotted from '../icons/ChatBubbleDotted.svelte';
	import ChatBubbleDottedChecked from '../icons/ChatBubbleDottedChecked.svelte';

	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import ChatPlus from '../icons/ChatPlus.svelte';
	import ChatCheck from '../icons/ChatCheck.svelte';
	import Knobs from '../icons/Knobs.svelte';
	import Share from '../icons/Share.svelte';
	import { isTemporaryChatId } from '$lib/utils/chatId';

	const i18n = getContext('i18n');

	const SHARE_URL = 'https://chat.vesqorai.com';
	const SHARE_TEXT = 'VESQOR MEGA AI — business intelligence engine. Messy problem in, structured report out.';

	const shareLinks = {
		facebook: `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(SHARE_URL)}`,
		x: `https://twitter.com/intent/tweet?url=${encodeURIComponent(SHARE_URL)}&text=${encodeURIComponent(SHARE_TEXT)}`,
		linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(SHARE_URL)}`,
		telegram: `https://t.me/share/url?url=${encodeURIComponent(SHARE_URL)}&text=${encodeURIComponent(SHARE_TEXT)}`
	};

	let showSocialShare = false;

	async function socialShare() {
		if (typeof navigator !== 'undefined' && navigator.share) {
			try {
				await navigator.share({ title: 'VESQOR MEGA AI', text: SHARE_TEXT, url: SHARE_URL });
				return;
			} catch {
				/* user cancelled */
			}
		}
		showSocialShare = !showSocialShare;
	}

	function openShare(network: 'facebook' | 'x' | 'linkedin' | 'telegram') {
		window.open(shareLinks[network], '_blank', 'noopener,noreferrer');
		showSocialShare = false;
	}

	export let initNewChat: Function;
	export let readOnly: boolean = false;
	export let shareEnabled: boolean = false;
	export let scrollTop = 0;
	export let scrollToTop: (() => void) | null = null;

	export let chat;
	export let history;
	export let title = '';
	export let onSaveTempChat: () => {};
	export let archiveChatHandler: (id: string) => void;
	export let deleteChatHandler: (id: string) => void;
	export let moveChatHandler: (id: string, folderId: string) => void;

	let closedBannerIds = [];

	const getDismissedBannerIds = (): string[] => {
		try {
			return JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]');
		} catch {
			return [];
		}
	};

	let showShareChatModal = false;
	let showDownloadChatModal = false;
</script>

<ShareChatModal bind:show={showShareChatModal} chatId={$chatId} />

<button
	id="new-chat-button"
	class="hidden"
	on:click={() => {
		initNewChat();
	}}
	aria-label="New Chat"
/>

<nav
	class="sticky top-0 z-30 w-full {$mobile
		? 'pt-1.5'
		: 'pt-0.5'} pb-1 -mb-12 flex flex-col items-center drag-region"
>
	<div class="flex items-center w-full {$mobile ? 'px-2.5' : 'pl-1.5 pr-1'}">
		<div
			id="navbar-bg-gradient-to-b"
			class="{chat?.id
				? 'visible'
				: 'invisible'} bg-linear-to-b via-40% to-97% from-white/90 via-white/50 to-transparent dark:from-gray-900/90 dark:via-gray-900/50 dark:to-transparent pointer-events-none absolute inset-0 -bottom-10 z-[-1]"
		></div>

		<div class=" flex max-w-full w-full mx-auto bg-transparent">
			<div class="flex items-center w-full max-w-full">
				{#if $mobile && !$showSidebar}
					<div class="mr-1 flex flex-none items-center self-center">
						<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
							<button
								id="sidebar-toggle-button"
								class="flex cursor-pointer rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							>
								<div class="self-center p-2">
									<Sidebar className="size-5" />
								</div>
							</button>
						</Tooltip>
					</div>
				{/if}

				<div
					class="flex-1 overflow-hidden max-w-full mt-0.5 py-0.5 pl-1 {$showSidebar ? 'ml-1' : ''}"
				>
					{#if chat?.id}
						<div class="relative flex max-w-full min-w-0 items-center gap-2 mr-2">
							<div
								class="min-w-0 truncate py-1 text-left text-base font-normal text-gray-700 dark:text-gray-300"
							>
								{title || chat?.chat?.title || $i18n.t('New Chat')}
							</div>

							{#if shareEnabled && chat && (chat.id || $temporaryChatEnabled)}
								<button
									class="flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
									aria-label={$i18n.t('Share on social networks')}
									on:click={socialShare}
								>
									<Share className="size-5" strokeWidth="1.5" />
								</button>
								{#if showSocialShare}
									<div
										class="absolute right-0 top-full z-50 mt-1 flex flex-col gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
									>
										<button
											class="flex items-center gap-2 rounded-md px-3 py-2 text-left text-base text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
											on:click={() => openShare('facebook')}
										>
											Facebook
										</button>
										<button
											class="flex items-center gap-2 rounded-md px-3 py-2 text-left text-base text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
											on:click={() => openShare('x')}
										>
											X (Twitter)
										</button>
										<button
											class="flex items-center gap-2 rounded-md px-3 py-2 text-left text-base text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
											on:click={() => openShare('linkedin')}
										>
											LinkedIn
										</button>
										<button
											class="flex items-center gap-2 rounded-md px-3 py-2 text-left text-base text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
											on:click={() => openShare('telegram')}
										>
											Telegram
										</button>
									</div>
								{/if}
								<Menu
									{chat}
									{shareEnabled}
									{readOnly}
									{scrollToTop}
									shareHandler={() => {
										showShareChatModal = !showShareChatModal;
									}}
									archiveChatHandler={() => {
										archiveChatHandler(chat.id);
									}}
									deleteChatHandler={() => {
										deleteChatHandler(chat.id);
									}}
									{moveChatHandler}
								>
									<button
										class="flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
										id="chat-context-menu-button"
										aria-label={$i18n.t('Chat actions')}
									>
										<EllipsisHorizontal className="size-5" strokeWidth="1.5" />
									</button>
								</Menu>
							{/if}
						</div>
					{:else}
						<div class="pointer-events-none invisible flex max-w-full min-w-0 items-center gap-2">
							<div
								class="min-w-0 truncate py-1 text-left text-base font-normal text-gray-700 dark:text-gray-300"
							>
								{$i18n.t('New Chat')}
							</div>
						</div>
					{/if}
				</div>

				<div class="lg:mr-1 flex flex-none items-center gap-2 self-center">
					<!-- <div class="md:hidden flex self-center w-[0.0625rem] h-5 mx-2 bg-gray-300 dark:bg-stone-700" /> -->

					{#if $user?.role === 'user' ? ($user?.permissions?.chat?.temporary ?? true) && !($user?.permissions?.chat?.temporary_enforced ?? false) : true}
						{#if !chat?.id}
							<Tooltip content={$i18n.t(`Temporary Chat`)}>
								<button
									class="flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
									id="temporary-chat-button"
									on:click={async () => {
										if (($settings?.temporaryChatByDefault ?? false) && $temporaryChatEnabled) {
											// for proper initNewChat handling
											await temporaryChatEnabled.set(null);
										} else {
											await temporaryChatEnabled.set(!$temporaryChatEnabled);
										}

										if ($page.url.pathname !== '/') {
											await goto('/');
										}

										// add 'temporary-chat=true' to the URL
										if ($temporaryChatEnabled) {
											window.history.replaceState(null, '', '?temporary-chat=true');
										} else {
											window.history.replaceState(null, '', location.pathname);
										}
									}}
									aria-label={$i18n.t(`Temporary Chat`)}
								>
									{#if $temporaryChatEnabled}
										<ChatBubbleDottedChecked className="size-5" strokeWidth="1.5" />
									{:else}
										<ChatBubbleDotted className="size-5" strokeWidth="1.5" />
									{/if}
								</button>
							</Tooltip>
						{:else if $temporaryChatEnabled}
							<Tooltip content={$i18n.t(`Save Chat`)}>
								<button
									class="flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
									id="save-temporary-chat-button"
									on:click={async () => {
										onSaveTempChat();
									}}
									aria-label={$i18n.t(`Save Chat`)}
								>
									<ChatCheck className="size-5" strokeWidth="1.5" />
								</button>
							</Tooltip>
						{/if}
					{/if}

					{#if $mobile && !$temporaryChatEnabled && chat && chat.id}
						<Tooltip content={$i18n.t('New Chat')}>
							<button
								class="flex size-6 {$showSidebar
									? 'md:hidden'
									: ''} cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
								on:click={() => {
									initNewChat();
								}}
								aria-label="New Chat"
							>
								<ChatPlus className="size-5" strokeWidth="1.5" />
							</button>
						</Tooltip>
					{/if}

					{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
						<Tooltip content={$i18n.t('Controls')}>
							<button
								class="flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
								on:click={async () => {
									await showControls.set(!$showControls);
								}}
								aria-label="Controls"
							>
								<Knobs className="size-6" strokeWidth="1" />
							</button>
						</Tooltip>
					{/if}
				</div>
			</div>
		</div>
	</div>

	{#if $temporaryChatEnabled && isTemporaryChatId($chatId)}
		<div class=" w-full z-30 text-center">
			<div class="text-xs text-gray-500">{$i18n.t('Temporary Chat')}</div>
		</div>
	{/if}

	<div class="absolute top-[100%] left-0 right-0 h-fit">
		{#if !history.currentId && !$chatId && ($banners.length > 0 || ($config?.license_metadata?.type ?? null) === 'trial' || (($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats))}
			<div class=" w-full z-30">
				<div
					class=" flex flex-col gap-1 w-full max-h-28 overflow-y-auto overscroll-contain md:max-h-none md:overflow-visible"
				>
					{#if ($config?.license_metadata?.type ?? null) === 'trial'}
						<Banner
							banner={{
								type: 'info',
								title: 'Trial License',
								content: $i18n.t(
									'You are currently using a trial license. Please contact support to upgrade your license.'
								)
							}}
						/>
					{/if}

					{#if ($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats}
						<Banner
							banner={{
								type: 'error',
								title: 'License Error',
								content: $i18n.t(
									'Exceeded the number of seats in your license. Please contact support to increase the number of seats.'
								)
							}}
						/>
					{/if}

					{#each $banners.filter((b) => ![...getDismissedBannerIds(), ...closedBannerIds].includes(b.id)) as banner (banner.id)}
						<Banner
							{banner}
							on:dismiss={(e) => {
								const bannerId = e.detail;

								if (banner.dismissible) {
									localStorage.setItem(
										'dismissedBannerIds',
										JSON.stringify(
											[bannerId, ...getDismissedBannerIds()].filter((id) =>
												$banners.find((b) => b.id === id)
											)
										)
									);
								} else {
									closedBannerIds = [...closedBannerIds, bannerId];
								}
							}}
						/>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</nav>
