<script lang="ts">
	// VESQOR MEGA AI — splash-видео при первом визите.
	// Показывается один раз (localStorage vesqor_splash_seen), блокирует
	// взаимодействие с приложением до конца видео, затем плавно исчезает.
	import { onMount, onDestroy, tick } from 'svelte';

	const STORAGE_KEY = 'vesqor_splash_seen';
	const VIDEO_SRC = '/static/vesqor-splash.mp4';
	const FADE_MS = 700;

	let show = false;
	let opacity = 1;
	let videoEl: HTMLVideoElement | undefined;
	let showSkip = false; // только если автоплей заблокирован браузером

	const blockScroll = () => {
		document.body.style.overflow = 'hidden';
	};

	const unblockScroll = () => {
		document.body.style.overflow = '';
	};

	const blockKeydown = (e: KeyboardEvent) => {
		e.preventDefault();
		e.stopPropagation();
	};

	const finish = () => {
		// fade-out
		opacity = 0;
		setTimeout(() => {
			unblockScroll();
			document.removeEventListener('keydown', blockKeydown, true);
			localStorage.setItem(STORAGE_KEY, '1');
			show = false;
		}, FADE_MS);
	};

	onMount(async () => {
		if (typeof window === 'undefined') return;
		// Только первый визит
		if (localStorage.getItem(STORAGE_KEY)) return;

		show = true;
		blockScroll();
		document.addEventListener('keydown', blockKeydown, true);

		// Ждём монтирования <video> (bind:this выполняется после onMount)
		await tick();

		if (!videoEl) return;

		const onEnded = () => finish();
		const onError = () => finish(); // не ломаем UX, если видео не загрузилось

		videoEl.addEventListener('ended', onEnded);
		videoEl.addEventListener('error', onError);

		try {
			await videoEl.play();
		} catch {
			// Автоплей заблокирован браузером — без кнопки пользователь
			// застрянет навсегда. Показываем «Пропустить» ТОЛЬКО здесь.
			showSkip = true;
		}
	});

	onDestroy(() => {
		document.removeEventListener('keydown', blockKeydown, true);
		unblockScroll();
	});
</script>

{#if show}
	<div
		class="vesqor-splash-overlay"
		style="opacity: {opacity}; transition: opacity {FADE_MS}ms ease;"
	>
		<video
			bind:this={videoEl}
			src={VIDEO_SRC}
			autoplay
			muted
			playsinline
			preload="auto"
			class="vesqor-splash-video"
		></video>
		{#if showSkip}
			<div class="vesqor-splash-skip" role="button" tabindex="-1" on:click={finish}>
				Пропустить
			</div>
		{/if}
	</div>
{/if}

<style>
	.vesqor-splash-overlay {
		position: fixed;
		inset: 0;
		z-index: 99999;
		background: #000;
		display: flex;
		align-items: center;
		justify-content: center;
		pointer-events: auto;
		user-select: none;
		cursor: default;
	}

	.vesqor-splash-video {
		width: 100%;
		height: 100%;
		object-fit: contain;
		background: #000;
	}

	.vesqor-splash-skip {
		position: absolute;
		bottom: 24px;
		right: 24px;
		color: rgba(255, 255, 255, 0.6);
		font-size: 13px;
		padding: 8px 16px;
		border-radius: 8px;
		cursor: pointer;
		background: rgba(255, 255, 255, 0.08);
		backdrop-filter: blur(4px);
		transition: background 0.2s, color 0.2s;
	}
	.vesqor-splash-skip:hover {
		background: rgba(255, 255, 255, 0.18);
		color: #fff;
	}
</style>
