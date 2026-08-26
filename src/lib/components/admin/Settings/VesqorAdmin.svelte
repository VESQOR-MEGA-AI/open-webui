<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getVesqorProviders,
		addVesqorProvider,
		updateVesqorProvider,
		deleteVesqorProvider,
		testVesqorProvider,
		getVesqorTokens,
		createVesqorToken,
		updateVesqorToken,
		deleteVesqorToken,
		getVesqorUsers,
		createVesqorUser,
		updateVesqorUser
	} from '$lib/apis/vesqor';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import AdminSettingField from './AdminSettingField.svelte';

	const i18n: any = getContext('i18n');

	type Section = 'providers' | 'tokens' | 'users';
	let section: Section = 'providers';

	// Providers
	let providers: any[] = [];
	let providersLoading = true;
	let providerForm = { name: '', baseUrl: '', apiKey: '', model: '', priority: 0 };
	let showAddProvider = false;
	let testingId: string | null = null;
	let testResults: Record<string, 'ok' | 'error'> = {};
	let showDeleteProviderConfirm = false;
	let providerToDelete: any = null;

	// Tokens
	let tokens: any[] = [];
	let tokensLoading = true;
	let tokenForm = { label: '', owner: '', limits: '' };
	let showAddToken = false;
	let newRawToken: string | null = null;
	let showDeleteTokenConfirm = false;
	let tokenToDelete: any = null;

	// Users
	let users: any[] = [];
	let usersLoading = true;
	let newUserEmail = '';
	let newUserVip = false;
	let creatingUser = false;
	let updatingUserId: string | null = null;

	const loadProviders = async () => {
		providersLoading = true;
		try {
			const res = await getVesqorProviders(localStorage.token);
			providers = Array.isArray(res) ? res : (res?.providers ?? []);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to load providers'));
		} finally {
			providersLoading = false;
		}
	};

	const loadTokens = async () => {
		tokensLoading = true;
		try {
			const res = await getVesqorTokens(localStorage.token);
			tokens = Array.isArray(res) ? res : (res?.tokens ?? []);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to load tokens'));
		} finally {
			tokensLoading = false;
		}
	};

	const addProvider = async () => {
		try {
			await addVesqorProvider(localStorage.token, {
				name: providerForm.name,
				baseUrl: providerForm.baseUrl,
				apiKey: providerForm.apiKey,
				model: providerForm.model,
				priority: Number(providerForm.priority) || 0
			});
			toast.success('Provider added');
			providerForm = { name: '', baseUrl: '', apiKey: '', model: '', priority: 0 };
			showAddProvider = false;
			await loadProviders();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to add provider'));
		}
	};

	const toggleProvider = async (provider: any) => {
		try {
			await updateVesqorProvider(localStorage.token, provider.id, { enabled: !provider.enabled });
			await loadProviders();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to update provider'));
		}
	};

	const testProvider = async (provider: any) => {
		testingId = provider.id;
		try {
			await testVesqorProvider(localStorage.token, provider.id);
			testResults = { ...testResults, [provider.id]: 'ok' };
		} catch (err) {
			testResults = { ...testResults, [provider.id]: 'error' };
		} finally {
			testingId = null;
		}
	};

	const confirmDeleteProvider = (provider: any) => {
		providerToDelete = provider;
		showDeleteProviderConfirm = true;
	};

	const deleteProvider = async () => {
		if (!providerToDelete) return;
		try {
			await deleteVesqorProvider(localStorage.token, providerToDelete.id);
			toast.success('Provider deleted');
			await loadProviders();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to delete provider'));
		} finally {
			providerToDelete = null;
		}
	};

	const addToken = async () => {
		try {
			const res = await createVesqorToken(localStorage.token, {
				label: tokenForm.label,
				owner: tokenForm.owner,
				limits: tokenForm.limits
			});
			newRawToken = res?.token ?? res?.rawToken ?? null;
			tokenForm = { label: '', owner: '', limits: '' };
			showAddToken = false;
			await loadTokens();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to create token'));
		}
	};

	const toggleToken = async (t: any) => {
		try {
			await updateVesqorToken(localStorage.token, t.id, { enabled: !t.enabled });
			await loadTokens();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to update token'));
		}
	};

	const confirmDeleteToken = (t: any) => {
		tokenToDelete = t;
		showDeleteTokenConfirm = true;
	};

	const deleteToken = async () => {
		if (!tokenToDelete) return;
		try {
			await deleteVesqorToken(localStorage.token, tokenToDelete.id);
			toast.success('Token deleted');
			await loadTokens();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to delete token'));
		} finally {
			tokenToDelete = null;
		}
	};

	const copyToken = async () => {
		if (newRawToken) {
			await navigator.clipboard.writeText(newRawToken);
			toast.success('Copied to clipboard');
		}
	};

	const loadUsers = async () => {
		usersLoading = true;
		try {
			const res = await getVesqorUsers(localStorage.token);
			users = Array.isArray(res) ? res : (res?.users ?? []);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to load users'));
		} finally {
			usersLoading = false;
		}
	};

	const addUser = async () => {
		const email = newUserEmail.trim();
		if (!email) return;

		creatingUser = true;
		try {
			await createVesqorUser(localStorage.token, { email, vip_access: newUserVip });
			toast.success('User added');
			newUserEmail = '';
			newUserVip = false;
			await loadUsers();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to add user'));
		} finally {
			creatingUser = false;
		}
	};

	const toggleUserVip = async (u: any) => {
		const previous = !!u.vipAccess;
		u.vipAccess = !previous;
		users = users;
		updatingUserId = u.id;
		try {
			await updateVesqorUser(localStorage.token, u.id, { vip_access: !previous });
			toast.success('VIP status updated');
		} catch (err: any) {
			u.vipAccess = previous;
			users = users;
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to update VIP status'));
		} finally {
			updatingUserId = null;
		}
	};

	onMount(() => {
		loadProviders();
		loadTokens();
		loadUsers();
	});
</script>

<ConfirmDialog
	bind:show={showDeleteProviderConfirm}
	title="Delete provider"
	message={`Delete provider "${providerToDelete?.name ?? ''}"? This cannot be undone.`}
	on:confirm={deleteProvider}
/>

<ConfirmDialog
	bind:show={showDeleteTokenConfirm}
	title="Delete token"
	message={`Delete token "${tokenToDelete?.label ?? ''}"? This cannot be undone.`}
	on:confirm={deleteToken}
/>

<div class="w-full h-full flex flex-col overflow-y-auto">
	<div class="flex gap-1 mb-3 text-sm">
		<button
			class="px-3 py-1 rounded-lg {section === 'providers'
				? 'bg-gray-100 dark:bg-gray-800 font-medium'
				: 'text-gray-500'}"
			on:click={() => (section = 'providers')}
		>
			Providers
		</button>
		<button
			class="px-3 py-1 rounded-lg {section === 'tokens'
				? 'bg-gray-100 dark:bg-gray-800 font-medium'
				: 'text-gray-500'}"
			on:click={() => (section = 'tokens')}
		>
			Tokens
		</button>
		<button
			class="px-3 py-1 rounded-lg {section === 'users'
				? 'bg-gray-100 dark:bg-gray-800 font-medium'
				: 'text-gray-500'}"
			on:click={() => (section = 'users')}
		>
			Users
		</button>
	</div>

	{#if section === 'providers'}
		<AdminSettingSection title="Provider pool" first>
			{#if providersLoading}
				<div class="flex justify-center py-6"><Spinner /></div>
			{:else}
				{#if providers.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-600 py-2">No providers yet.</div>
				{/if}
				{#each providers as provider (provider.id)}
					<div
						class="flex items-center justify-between gap-2 py-2 px-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
					>
						<div class="min-w-0">
							<div class="text-xs font-medium truncate">{provider.name}</div>
							<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
								{provider.baseUrl} · {provider.model} · priority {provider.priority ?? 0}
							</div>
							{#if testResults[provider.id]}
								<div
									class="text-[0.6875rem] {testResults[provider.id] === 'ok'
										? 'text-green-600'
										: 'text-red-500'}"
								>
									{testResults[provider.id] === 'ok' ? 'Test passed' : 'Test failed'}
								</div>
							{/if}
						</div>
						<div class="flex items-center gap-2 shrink-0">
							<button
								class="text-xs px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-800 disabled:opacity-50"
								disabled={testingId === provider.id}
								on:click={() => testProvider(provider)}
							>
								Test
							</button>
							<Switch state={!!provider.enabled} on:change={() => toggleProvider(provider)} />
							<button
								class="text-xs px-2 py-1 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-950"
								on:click={() => confirmDeleteProvider(provider)}
							>
								Delete
							</button>
						</div>
					</div>
				{/each}
			{/if}

			{#if showAddProvider}
				<form
					class="flex flex-col gap-2 mt-2 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
					on:submit|preventDefault={addProvider}
				>
					<AdminSettingField label="Name">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={providerForm.name}
							required
						/>
					</AdminSettingField>
					<AdminSettingField label="Base URL">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={providerForm.baseUrl}
							required
						/>
					</AdminSettingField>
					<AdminSettingField label="API Key">
						<input
							type="password"
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={providerForm.apiKey}
						/>
					</AdminSettingField>
					<AdminSettingField label="Model">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={providerForm.model}
						/>
					</AdminSettingField>
					<AdminSettingField label="Priority">
						<input
							type="number"
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={providerForm.priority}
						/>
					</AdminSettingField>
					<div class="flex gap-2 justify-end mt-1">
						<button
							type="button"
							class="text-xs px-2.5 py-1 rounded-lg bg-gray-100 dark:bg-gray-800"
							on:click={() => (showAddProvider = false)}
						>
							Cancel
						</button>
						<button
							type="submit"
							class="text-xs px-2.5 py-1 rounded-lg bg-black text-white dark:bg-white dark:text-black"
						>
							Add
						</button>
					</div>
				</form>
			{:else}
				<button
					class="text-xs px-2.5 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 self-start mt-1"
					on:click={() => (showAddProvider = true)}
				>
					+ Add provider
				</button>
			{/if}
		</AdminSettingSection>
	{:else if section === 'tokens'}
		<AdminSettingSection title="Agent tokens" first>
			{#if newRawToken}
				<div
					class="flex items-center justify-between gap-2 p-2.5 rounded-lg bg-yellow-50 dark:bg-yellow-950/40 border border-yellow-200 dark:border-yellow-900"
				>
					<code class="text-xs break-all">{newRawToken}</code>
					<button
						class="text-xs px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-800 shrink-0"
						on:click={copyToken}
					>
						Copy
					</button>
				</div>
				<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
					This token is shown only once. Store it securely.
				</div>
			{/if}

			{#if tokensLoading}
				<div class="flex justify-center py-6"><Spinner /></div>
			{:else}
				{#if tokens.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-600 py-2">No tokens yet.</div>
				{/if}
				{#each tokens as t (t.id)}
					<div
						class="flex items-center justify-between gap-2 py-2 px-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
					>
						<div class="min-w-0">
							<div class="text-xs font-medium truncate">{t.label}</div>
							<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
								{t.owner ?? ''} {t.limits ? `· ${t.limits}` : ''}
							</div>
						</div>
						<div class="flex items-center gap-2 shrink-0">
							<Switch state={!!t.enabled} on:change={() => toggleToken(t)} />
							<button
								class="text-xs px-2 py-1 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-950"
								on:click={() => confirmDeleteToken(t)}
							>
								Delete
							</button>
						</div>
					</div>
				{/each}
			{/if}

			{#if showAddToken}
				<form
					class="flex flex-col gap-2 mt-2 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
					on:submit|preventDefault={addToken}
				>
					<AdminSettingField label="Label">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={tokenForm.label}
							required
						/>
					</AdminSettingField>
					<AdminSettingField label="Owner">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={tokenForm.owner}
						/>
					</AdminSettingField>
					<AdminSettingField label="Limits">
						<input
							class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
							bind:value={tokenForm.limits}
						/>
					</AdminSettingField>
					<div class="flex gap-2 justify-end mt-1">
						<button
							type="button"
							class="text-xs px-2.5 py-1 rounded-lg bg-gray-100 dark:bg-gray-800"
							on:click={() => (showAddToken = false)}
						>
							Cancel
						</button>
						<button
							type="submit"
							class="text-xs px-2.5 py-1 rounded-lg bg-black text-white dark:bg-white dark:text-black"
						>
							Create
						</button>
					</div>
				</form>
			{:else}
				<button
					class="text-xs px-2.5 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 self-start mt-1"
					on:click={() => {
						showAddToken = true;
						newRawToken = null;
					}}
				>
					+ Create token
				</button>
			{/if}
		</AdminSettingSection>
	{:else if section === 'users'}
		<AdminSettingSection title="Users" first>
			{#if usersLoading}
				<div class="flex justify-center py-6"><Spinner /></div>
			{:else}
				{#if users.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-600 py-2">No users yet.</div>
				{/if}
				{#each users as u (u.id)}
					<div
						class="flex items-center justify-between gap-2 py-2 px-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
					>
						<div class="min-w-0">
							<div class="text-xs font-medium truncate">{u.email}</div>
							<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
								{u.role ?? 'user'}{u.createdAt
									? ` · ${new Date(u.createdAt).toLocaleDateString()}`
									: ''}{u.hasSubscription ? ' · subscribed' : ''}
							</div>
						</div>
						<div class="flex items-center gap-2 shrink-0">
							{#if updatingUserId === u.id}
								<Spinner className="size-3.5" />
							{/if}
							<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600">VIP</span>
							<Switch state={!!u.vipAccess} on:change={() => toggleUserVip(u)} />
						</div>
					</div>
				{/each}
			{/if}

			<form
				class="flex flex-col gap-2 mt-2 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-850"
				on:submit|preventDefault={addUser}
			>
				<AdminSettingField label="Email">
					<input
						type="email"
						class="w-full text-xs bg-transparent outline-hidden border rounded-lg px-2 py-1 dark:border-gray-700"
						bind:value={newUserEmail}
						required
					/>
				</AdminSettingField>
				<div class="flex items-center gap-2">
					<Switch bind:state={newUserVip} />
					<span class="text-xs">VIP free access</span>
				</div>
				<div class="flex gap-2 justify-end mt-1">
					<button
						type="submit"
						disabled={creatingUser}
						class="text-xs px-2.5 py-1 rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
					>
						+ Add user
					</button>
				</div>
			</form>
		</AdminSettingSection>
	{/if}
</div>
