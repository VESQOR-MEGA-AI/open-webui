<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import dayjs from '$lib/dayjs';

	import { goto } from '$app/navigation';
	import { WEBUI_NAME, showSidebar, mobile } from '$lib/stores';
	import { getLibrary, deleteLibraryItem, downloadLibraryItem, type LibraryItem } from '$lib/apis/library';
	import { formatFileSize } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import Trash from '$lib/components/icons/Trash.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	let items: LibraryItem[] | null = null;

	let query = '';
	let typeFilter: 'all' | 'report' | 'file' = 'all';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let showDeleteConfirm = false;
	let deleteTarget: LibraryItem | null = null;

	$: groups = groupByChat(items ?? []);

	const groupByChat = (list: LibraryItem[]) => {
		const map = new Map<string, { title: string; chatId: string | null; items: LibraryItem[] }>();

		for (const item of list) {
			const key = item.chat_id || '';
			if (!map.has(key)) {
				map.set(key, {
					title: key ? item.chat_title || key : $i18n.t('My uploads'),
					chatId: key || null,
					items: []
				});
			}
			map.get(key)?.items.push(item);
		}

		return Array.from(map.values());
	};

	const fetchLibrary = async () => {
		const res = await getLibrary(localStorage.token, {
			q: query || undefined,
			type: typeFilter === 'all' ? undefined : typeFilter
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			items = res;
		}
	};

	const handleSearchInput = () => {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			fetchLibrary();
		}, 300);
	};

	const downloadHandler = async (item: LibraryItem) => {
		const res = await downloadLibraryItem(localStorage.token, item.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (!res || !res.ok) {
			toast.error($i18n.t('Failed to download file'));
			return;
		}

		const blob = await res.blob();
		saveAs(blob, item.filename);
	};

	const deleteHandler = async (item: LibraryItem) => {
		const res = await deleteLibraryItem(localStorage.token, item.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Deleted {{name}}', { name: item.filename }));
			fetchLibrary();
		}
	};

	onMount(async () => {
		await fetchLibrary();
		loaded = true;
	});
</script>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete item?')}
	on:confirm={() => {
		if (deleteTarget) deleteHandler(deleteTarget);
	}}
>
	<div class="text-sm text-gray-500 truncate">
		{$i18n.t('This will delete')} <span class="font-normal">{deleteTarget?.filename}</span>.
	</div>
</ConfirmDialog>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex h-full min-h-0 flex-col">
		<div class="shrink-0 px-2.5 pt-2 pb-1">
			<div class="flex items-center gap-0.5 md:gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
						<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
							<button
								id="sidebar-toggle-button"
								class="flex size-7 items-center justify-center text-gray-400 transition"
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
								type="button"
							>
								<SidebarIcon className="size-4" />
							</button>
						</Tooltip>
					</div>
				{/if}

				<div class="flex w-full min-w-0 items-center">
					<div class="flex min-w-0 flex-1 items-center gap-1 py-1">
						<span class="min-w-fit px-1 text-sm select-none">{$i18n.t('Library')}</span>
					</div>
				</div>
			</div>
		</div>

		<div class="min-h-0 flex-1 overflow-y-auto px-2.5 pb-1">
			{#if loaded}
				<div class="space-y-1">
					<div class="flex h-8 flex-1 items-center w-full gap-2">
						<div class="flex min-w-0 flex-1 items-center">
							<div class="self-center ml-1 mr-3">
								<Search className="size-3.5" />
							</div>
							<input
								class="w-full text-sm py-1 rounded-r-xl outline-hidden bg-transparent"
								bind:value={query}
								on:input={handleSearchInput}
								aria-label={$i18n.t('Search Library')}
								placeholder={$i18n.t('Search Library')}
								maxlength="500"
							/>

							{#if query}
								<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
									<button
										class="p-0.5 rounded-full transition"
										aria-label={$i18n.t('Clear search')}
										on:click={() => {
											query = '';
											handleSearchInput();
										}}
									>
										<XMark className="size-3" strokeWidth="2" />
									</button>
								</div>
							{/if}
						</div>

						<div class="flex shrink-0 gap-0.5 text-center text-sm rounded-full whitespace-nowrap">
							{#each [{ value: 'all', label: $i18n.t('All') }, { value: 'report', label: $i18n.t('Reports') }, { value: 'file', label: $i18n.t('Documents') }] as tab}
								<button
									class="min-w-fit p-1.5 {typeFilter === tab.value
										? 'bg-gray-100 dark:bg-gray-800'
										: 'bg-transparent text-gray-500'} rounded-xl transition"
									type="button"
									on:click={() => {
										typeFilter = tab.value as typeof typeFilter;
										fetchLibrary();
									}}
								>
									{tab.label}
								</button>
							{/each}
						</div>
					</div>

					{#if items === null}
						<div class="flex min-h-[calc(100dvh-13rem)] w-full items-center justify-center">
							<Spinner className="size-5" />
						</div>
					{:else if groups.length === 0}
						<div class="flex min-h-[calc(100dvh-13rem)] w-full flex-col items-center justify-center">
							<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
								<div class="mb-1.5 text-sm">
									{query ? $i18n.t('No results found') : $i18n.t('No items in your Library yet')}
								</div>
								<div class="text-center text-xs leading-5 text-gray-500">
									{$i18n.t('Exported reports and uploaded documents will appear here.')}
								</div>
							</div>
						</div>
					{:else}
						<div class="my-1 space-y-4">
							{#each groups as group (group.chatId ?? 'uploads')}
								<div>
									<div class="flex items-center gap-1.5 px-1 py-1 text-xs text-gray-500 dark:text-gray-400">
										<Folder className="size-3.5" />
										<span class="truncate">{group.title}</span>
									</div>

									<div class="gap-y-0.5 grid">
										{#each group.items as item (item.id)}
											<div
												class="group flex min-h-8 w-full items-center gap-2 overflow-hidden rounded-xl px-2 py-1 text-left"
											>
												<div class="flex shrink-0 items-center text-gray-400">
													<Document className="size-4" />
												</div>

												<div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
													<Tooltip content={item.filename} className="min-w-0" placement="top-start">
														<div
															class="truncate text-[13px] leading-5 text-gray-800 dark:text-gray-200"
														>
															{item.filename}
														</div>
													</Tooltip>
												</div>

												<div
													class="hidden shrink-0 truncate text-[11px] leading-5 text-gray-400 dark:text-gray-600 sm:block"
												>
													{formatFileSize(item.size)}
												</div>

												<div
													class="hidden shrink-0 truncate text-[11px] leading-5 text-gray-400 dark:text-gray-600 md:block"
												>
													{dayjs(item.created_at).format('MMM D, YYYY h:mm A')}
												</div>

												<div class="flex shrink-0 flex-row items-center gap-1 self-center">
													{#if group.chatId}
														<Tooltip content={$i18n.t('Open in chat')}>
															<button
																class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition hover:text-gray-700 dark:hover:text-gray-200"
																type="button"
																aria-label={$i18n.t('Open in chat')}
																on:click={() => goto(`/c/${group.chatId}`)}
															>
																<Folder className="size-3.5" />
															</button>
														</Tooltip>
													{/if}

													<Tooltip content={$i18n.t('Download')}>
														<button
															class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition hover:text-gray-700 dark:hover:text-gray-200"
															type="button"
															aria-label={$i18n.t('Download')}
															on:click={() => downloadHandler(item)}
														>
															<Download className="size-3.5" />
														</button>
													</Tooltip>

													<Tooltip content={$i18n.t('Delete')}>
														<button
															class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition hover:text-red-600"
															type="button"
															aria-label={$i18n.t('Delete')}
															on:click={() => {
																deleteTarget = item;
																showDeleteConfirm = true;
															}}
														>
															<Trash className="size-3.5" />
														</button>
													</Tooltip>
												</div>
											</div>
										{/each}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{:else}
				<div class="w-full h-full flex justify-center items-center">
					<Spinner className="size-5" />
				</div>
			{/if}
		</div>
	</div>
</div>
