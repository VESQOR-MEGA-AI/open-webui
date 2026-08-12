<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	export let show = true;
	export let getStartedHandler = () => {};

	const capabilities = [
		{
			title: 'Problem decomposition',
			desc: 'Breaks a messy multi-part situation into independent and dependent pieces, addresses each correctly'
		},
		{
			title: 'Gap identification',
			desc: "Names what's missing from the input rather than inventing plausible-sounding filler"
		},
		{
			title: 'Structured reporting',
			desc: 'Delivers findings in the format the task actually needs — tables, roadmaps, risk registers, workflows, code blocks, diagrams'
		},
		{
			title: 'Honest confidence scoring',
			desc: 'States clearly when an answer rests on an assumption, rather than projecting false certainty'
		},
		{
			title: 'Audience adaptation',
			desc: 'Matches technical depth to the reader — executive summary for leadership, practitioner detail for operators'
		},
		{
			title: 'Domain breadth',
			desc: 'Operations, go-to-market, technical architecture, compliance/risk, market analysis, financial planning context'
		}
	];

	const useCases = [
		'Analyze a business problem or decision',
		'Turn a messy idea into a clear plan',
		'Upload a document and ask questions about it',
		'Get a second opinion on your thinking'
	];

	const whyItems = [
		{
			title: 'Clear, structured answers',
			desc: 'Every report is organized so you can scan it in seconds.'
		},
		{
			title: 'Plain-language questions',
			desc: 'No templates or jargon — just describe what you need.'
		},
		{
			title: 'Your data stays yours',
			desc: 'Reports and documents are saved to your private account.'
		}
	];

	const poweredBy = [
		'Claude',
		'DeepSeek',
		'Qwen',
		'Mistral',
		'Meta Llama',
		'NVIDIA',
		'Google Cloud',
		'Hugging Face',
		'OpenRouter',
		'Ollama',
		'vLLM'
	];
</script>

{#if show}
	<div class="relative z-[60] min-h-screen w-full overflow-hidden bg-[#0a0a12] text-white">
		<!-- Background: dark navy gradient + circuit pattern -->
		<div class="pointer-events-none absolute inset-0">
			<div class="absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(30,60,140,0.25),transparent_60%)]"></div>
			<div class="absolute inset-0 opacity-[0.06] [background-image:linear-gradient(rgba(80,140,255,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(80,140,255,0.5)_1px,transparent_1px)] [background-size:44px_44px]"></div>
		</div>

		<!-- Top bar -->
		<div class="relative z-10 flex items-center justify-between px-6 py-5 sm:px-10">
			<div class="flex items-center gap-3">
				<img
					id="logo"
					crossorigin="anonymous"
					src="/static/favicon.png"
					class="size-9 rounded-full"
					alt="VESQOR MEGA AI logo"
				/>
				<span class="text-sm font-medium tracking-[0.2em] uppercase text-white/80">VESQOR MEGA AI</span>
			</div>
			<button
				class="rounded-full border border-white/20 px-5 py-2 text-sm text-white/80 transition hover:border-white/50 hover:text-white"
				on:click={() => {
					getStartedHandler();
				}}
			>
				{$i18n.t('Sign in')}
			</button>
		</div>

		<!-- Hero -->
		<section class="relative z-10 mx-auto max-w-5xl px-6 pt-16 pb-20 text-center sm:pt-24">
			<div class="mx-auto mb-8 flex w-fit items-center justify-center">
				<img
					src="/static/logo.png"
					class="h-24 w-24 rounded-2xl sm:h-32 sm:w-32"
					alt="VESQOR MEGA AI"
				/>
			</div>
			<h1 class="mx-auto max-w-3xl text-4xl leading-[1.1] font-light tracking-tight sm:text-5xl">
				Your thinking partner for <span class="bg-gradient-to-r from-sky-400 to-blue-500 bg-clip-text text-transparent">business decisions</span>
			</h1>
			<p class="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-white/60 sm:text-lg">
				Describe a problem, a document, or a half-formed idea. VESQOR MEGA AI turns it into a clear, structured answer you can act on.
			</p>
			<div class="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
				<button
					class="group inline-flex min-w-44 items-center justify-center gap-2 rounded-full bg-gradient-to-r from-sky-500 to-blue-600 px-8 py-3.5 text-sm font-medium text-white transition hover:from-sky-400 hover:to-blue-500 focus:ring-2 focus:ring-sky-400/50 focus:outline-hidden"
					on:click={() => {
						getStartedHandler();
					}}
				>
					{$i18n.t('Get started')}
					<svg class="h-4 w-4 transition group-hover:translate-x-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
						<path stroke-linecap="round" stroke-linejoin="round" d="M17 8l4 4m0 0l-4 4m4-4H3" />
					</svg>
				</button>
			</div>
		</section>

		<!-- Not another AI chat -->
		<section class="relative z-10 mx-auto max-w-5xl px-6 py-16">
			<h2 class="text-center text-2xl font-light tracking-tight sm:text-3xl">Not another AI chat</h2>
			<p class="mx-auto mt-4 max-w-2xl text-center text-sm leading-relaxed text-white/60 sm:text-base">
				ChatGPT answers whatever you type — then you do the work. VESQOR MEGA AI is built for one job: turning a messy business problem into a clear, structured answer.
			</p>
			<div class="mt-10 grid gap-6 sm:grid-cols-2">
				<div class="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
					<div class="mb-4 text-xs font-medium tracking-[0.15em] uppercase text-white/40">With a generic AI chat</div>
					<ul class="space-y-3 text-sm text-white/70">
						<li class="flex gap-3"><span class="text-white/30">•</span> You drive: question after question, correcting and re-asking</li>
						<li class="flex gap-3"><span class="text-white/30">•</span> Often an hour or more to reach a straight answer</li>
						<li class="flex gap-3"><span class="text-white/30">•</span> Guesses the parts of your problem it does not know — and you cannot always tell</li>
					</ul>
				</div>
				<div class="rounded-2xl border border-sky-400/30 bg-sky-400/[0.06] p-6">
					<div class="mb-4 text-xs font-medium tracking-[0.15em] uppercase text-sky-300">With VESQOR MEGA AI</div>
					<ul class="space-y-3 text-sm text-white/80">
						<li class="flex gap-3"><span class="text-sky-300">•</span> One question. A structured answer in seconds.</li>
						<li class="flex gap-3"><span class="text-sky-300">•</span> The full picture: core issue, approach, recommendation</li>
						<li class="flex gap-3"><span class="text-sky-300">•</span> Built for business decisions — not small talk</li>
					</ul>
				</div>
			</div>
		</section>

		<!-- What VESQOR MEGA AI Is -->
		<section class="relative z-10 mx-auto max-w-5xl px-6 py-16">
			<h2 class="text-center text-2xl font-light tracking-tight sm:text-3xl">What VESQOR MEGA AI Is</h2>
			<p class="mx-auto mt-4 max-w-3xl text-center text-sm leading-relaxed text-white/60 sm:text-base">
				VESQOR MEGA AI is a business intelligence engine built to take real-world business, technical, and operational problems — messy, incomplete, half-formed — and return a single professional report a decision-maker can act on immediately.
			</p>
			<p class="mx-auto mt-4 max-w-3xl text-center text-sm leading-relaxed text-white/60 sm:text-base">
				It is not a chatbot, not a search tool, not a coding assistant. It is a problem-solving engine for the kinds of situations that are too particular for a generic playbook.
			</p>
			<div class="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each capabilities as cap}
					<div class="rounded-xl border border-white/10 bg-white/[0.03] p-5">
						<div class="mb-2 text-sm font-medium text-sky-300">{cap.title}</div>
						<div class="text-sm leading-relaxed text-white/60">{cap.desc}</div>
					</div>
				{/each}
			</div>
			<p class="mx-auto mt-8 max-w-3xl text-center text-xs leading-relaxed text-white/40">
				What VESQOR does not do: It does not make decisions for the people who own them. It does not take actions in the world. It does not reach outside itself for live data. It does not pretend to know things it does not know.
			</p>
		</section>

		<!-- Powered by -->
		<section class="relative z-10 mx-auto max-w-5xl px-6 py-16">
			<div class="mb-6 text-center text-xs font-medium tracking-[0.2em] uppercase text-white/40">Powered by</div>
			<div class="flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
				{#each poweredBy as name}
					<span class="text-sm font-medium text-white/50 transition hover:text-white/80">{name}</span>
				{/each}
			</div>
		</section>

		<!-- How it helps -->
		<section class="relative z-10 mx-auto max-w-5xl px-6 py-16">
			<h2 class="text-center text-2xl font-light tracking-tight sm:text-3xl">How it helps you</h2>
			<p class="mx-auto mt-4 max-w-2xl text-center text-sm leading-relaxed text-white/60 sm:text-base">
				Instead of staring at a blank page, you get a well-organized answer you can review, refine, and act on — in minutes, not hours.
			</p>
			<div class="mt-10 grid gap-6 sm:grid-cols-3">
				{#each whyItems as item}
					<div class="rounded-xl border border-white/10 bg-white/[0.03] p-6 text-center">
						<div class="mb-2 text-sm font-medium text-white">{item.title}</div>
						<div class="text-sm leading-relaxed text-white/50">{item.desc}</div>
					</div>
				{/each}
			</div>
			<div class="mt-10 flex flex-wrap items-center justify-center gap-3">
				{#each useCases as uc}
					<span class="rounded-full border border-white/15 px-4 py-2 text-sm text-white/70">{uc}</span>
				{/each}
			</div>
		</section>

		<!-- CTA -->
		<section class="relative z-10 mx-auto max-w-3xl px-6 py-20 text-center">
			<h2 class="text-2xl font-light tracking-tight sm:text-3xl">Messy problem in. Structured report out.</h2>
			<p class="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-white/60 sm:text-base">
				Real questions, real reports — generated by VESQOR MEGA AI.
			</p>
			<div class="mt-8">
				<button
					class="inline-flex min-w-44 items-center justify-center gap-2 rounded-full bg-gradient-to-r from-sky-500 to-blue-600 px-8 py-3.5 text-sm font-medium text-white transition hover:from-sky-400 hover:to-blue-500 focus:ring-2 focus:ring-sky-400/50 focus:outline-hidden"
					on:click={() => {
						getStartedHandler();
					}}
				>
					{$i18n.t('Get started')}
				</button>
			</div>
		</section>

		<!-- Footer -->
		<footer class="relative z-10 border-t border-white/10 px-6 py-8">
			<div class="mx-auto flex max-w-5xl flex-col items-center justify-between gap-4 sm:flex-row">
				<div class="flex items-center gap-2">
					<img src="/static/favicon.png" class="size-5 rounded-full" alt="" />
					<span class="text-xs text-white/40">© 2026 VESQOR MEGA AI</span>
				</div>
				<div class="flex gap-6 text-xs text-white/40">
					<a href="/legal/terms" class="transition hover:text-white/70">Terms</a>
					<a href="/legal/privacy" class="transition hover:text-white/70">Privacy</a>
					<a href="/contact" class="transition hover:text-white/70">Contact</a>
				</div>
			</div>
		</footer>
	</div>
{/if}
