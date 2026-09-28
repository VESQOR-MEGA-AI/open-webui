<script lang="ts">
	/**
	 * VESQOR thinking placeholder — shown while an assistant message has no
	 * content yet. A live matrix rain in the VESQOR brand greens, the latest
	 * one-line pipeline ticker from the brain ("Recall → no cached match",
	 * "Model → 1.2k tokens out") and the percent of the whole task.
	 *
	 * The brain sends its progress as Open WebUI `status` events tagged
	 * `action: 'vq_progress'` with a `percent` field (vesqor-core
	 * lib/brain/progress.ts). Between two events the percent creeps slowly
	 * toward the next milestone so the counter never looks frozen, but it never
	 * reaches 100 until the brain says so.
	 */
	import { onDestroy, onMount } from 'svelte';

	export let statusHistory: any[] = [];

	const GLYPHS = 'VESQOR0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<>/*+=-|:'.split('');
	// Brand palette (vesqor-core MatrixRainBackground): brightest, bright, primary, muted.
	const HEAD = '#eafff2';
	const NEAR = '#7ef0b0';
	const BODY = [57, 181, 74]; // #39b54a
	const TAIL = [31, 122, 61]; // #1f7a3d

	$: progressEntries = (statusHistory ?? []).filter((s) => s?.action === 'vq_progress');
	$: latest = progressEntries.at(-1);
	$: ticker = progressEntries.slice(-3).map((s) => s.description);
	$: target = Math.max(0, Math.min(100, Number(latest?.percent ?? 0)));

	let shown = 0;
	let canvas: HTMLCanvasElement;
	let host: HTMLDivElement;
	let raf = 0;
	let visible = true;
	let observer: IntersectionObserver | null = null;
	let resizeObserver: ResizeObserver | null = null;
	const reduceMotion =
		typeof window !== 'undefined' &&
		window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;

	type Column = { y: number; speed: number; len: number; chars: string[] };
	let columns: Column[] = [];
	const cell = 14;
	let lastFrame = 0;

	const pick = () => GLYPHS[(Math.random() * GLYPHS.length) | 0];

	function layout() {
		if (!canvas || !host) return;
		const dpr = Math.min(window.devicePixelRatio || 1, 2);
		const w = host.clientWidth;
		const h = host.clientHeight;
		canvas.width = Math.round(w * dpr);
		canvas.height = Math.round(h * dpr);
		canvas.style.width = `${w}px`;
		canvas.style.height = `${h}px`;
		const ctx = canvas.getContext('2d');
		ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
		const count = Math.ceil(w / cell);
		const rows = Math.ceil(h / cell);
		columns = Array.from({ length: count }, () => ({
			y: -Math.random() * rows,
			speed: 6 + Math.random() * 14,
			len: 6 + ((Math.random() * 16) | 0),
			chars: Array.from({ length: rows + 24 }, pick)
		}));
	}

	function draw(t: number) {
		const ctx = canvas?.getContext('2d');
		if (!ctx || !host) return;
		const dt = lastFrame ? Math.min(0.1, (t - lastFrame) / 1000) : 0;
		lastFrame = t;
		const w = host.clientWidth;
		const h = host.clientHeight;
		const rows = Math.ceil(h / cell);
		ctx.clearRect(0, 0, w, h);
		ctx.font = `600 ${cell - 2}px ui-monospace, SFMono-Regular, Menlo, monospace`;
		ctx.textBaseline = 'top';

		// Completion sweeps the rain from left to right, like the bar below it:
		// columns past the progress line stay dim.
		const litEdge = (shown / 100) * w;

		for (let i = 0; i < columns.length; i++) {
			const c = columns[i];
			c.y += c.speed * dt;
			if (c.y - c.len > rows) {
				c.y = -Math.random() * 8;
				c.speed = 6 + Math.random() * 14;
				c.len = 6 + ((Math.random() * 16) | 0);
			}
			if (Math.random() < 0.04) c.chars[(Math.random() * c.chars.length) | 0] = pick();
			const x = i * cell;
			const lit = x <= litEdge ? 1 : 0.35;
			const head = Math.floor(c.y);
			for (let k = 0; k < c.len; k++) {
				const row = head - k;
				if (row < 0 || row > rows) continue;
				const age = k / c.len;
				const a = Math.pow(1 - age, 1.6) * lit;
				if (k === 0) ctx.fillStyle = HEAD;
				else if (k < 3) ctx.fillStyle = NEAR;
				else {
					const [r, g, b] = age < 0.5 ? BODY : TAIL;
					ctx.fillStyle = `rgba(${r},${g},${b},${a.toFixed(3)})`;
				}
				ctx.globalAlpha = k < 3 ? Math.max(0.15, lit) : 1;
				ctx.fillText(c.chars[row % c.chars.length], x, row * cell);
			}
		}
		ctx.globalAlpha = 1;
	}

	function tick(t: number) {
		// Ease toward the brain's last milestone; between milestones keep the
		// bar visibly alive (rate 0.004 read as frozen — 0.015 lands visibly
		// within seconds but still never crosses the next real milestone).
		const ceiling = target >= 100 ? 100 : Math.min(99, target + 6);
		const goal = shown < target ? target : ceiling;
		const rate = shown < target ? 0.12 : 0.015;
		shown = Math.min(goal, shown + Math.max(0, goal - shown) * rate);
		if (visible && !reduceMotion) draw(t);
		raf = requestAnimationFrame(tick);
	}

	onMount(() => {
		shown = target;
		layout();
		if (reduceMotion) draw(performance.now());
		resizeObserver = new ResizeObserver(() => {
			layout();
			if (reduceMotion) draw(performance.now());
		});
		resizeObserver.observe(host);
		observer = new IntersectionObserver((entries) => {
			visible = entries.some((e) => e.isIntersecting);
			lastFrame = 0;
		});
		observer.observe(host);
		raf = requestAnimationFrame(tick);
	});

	onDestroy(() => {
		if (typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(raf);
		observer?.disconnect();
		resizeObserver?.disconnect();
	});
</script>

<div
	class="vq-thinking relative w-full overflow-hidden rounded-2xl my-1"
	role="status"
	aria-live="polite"
	aria-label={`${latest?.description ?? 'Thinking'} · ${Math.floor(shown)}%`}
>
	<div bind:this={host} class="vq-rain absolute inset-0" aria-hidden="true">
		<canvas bind:this={canvas} class="block"></canvas>
	</div>

	<div class="vq-scrim absolute inset-x-0 bottom-0 h-32" aria-hidden="true"></div>

	<div class="relative flex flex-col justify-end h-full px-3 pb-3 pt-2">
		<div class="flex flex-col gap-0.5 font-mono text-xs">
			{#each ticker as line, idx (idx + line)}
				<div
					class="line-clamp-1 {idx === ticker.length - 1
						? 'text-[#eafff2] shimmer'
						: 'text-[#7ef0b0]/60'}"
				>
					{line}
				</div>
			{:else}
				<div class="line-clamp-1 text-[#eafff2] shimmer">VESQOR → thinking</div>
			{/each}
		</div>

		<div class="mt-2 flex items-center gap-2">
			<div class="h-1 flex-1 rounded-full bg-[#1f7a3d]/30 overflow-hidden">
				<div
					class="h-full rounded-full bg-gradient-to-r from-[#1f7a3d] via-[#39b54a] to-[#7ef0b0]"
					style="width: {shown}%"
				></div>
			</div>
			<div
				class="shrink-0 rounded-full bg-black/50 px-2 py-0.5 text-xs font-semibold tabular-nums text-[#eafff2]"
			>
				{Math.floor(shown)}%
			</div>
		</div>
	</div>
</div>

<style>
	.vq-thinking {
		height: 16rem;
		background: #0b1f15;
	}
	.vq-scrim {
		background: linear-gradient(to top, #0b1f15 0%, #0b1f15 55%, rgba(11, 31, 21, 0) 100%);
	}
	.vq-rain {
		/* Fade the rain out toward the right edge and the ticker at the bottom. */
		mask-image:
			linear-gradient(to right, #000 55%, transparent 100%),
			linear-gradient(to bottom, #000 60%, transparent 100%);
		mask-composite: intersect;
		-webkit-mask-image:
			linear-gradient(to right, #000 55%, transparent 100%),
			linear-gradient(to bottom, #000 60%, transparent 100%);
		-webkit-mask-composite: source-in;
	}
</style>
