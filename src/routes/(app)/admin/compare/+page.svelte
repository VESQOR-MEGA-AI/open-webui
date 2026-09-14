<script lang="ts">
	import AnswerCompare from '$lib/components/admin/AnswerCompare.svelte';
	import MatrixRainStage from '$lib/components/admin/MatrixRainStage.svelte';
</script>

<!-- VESQOR: живой Matrix rain на странице сравнения (owner 2026-09-14). -->
<MatrixRainStage />
<div class="vesqor-typing-matte" aria-hidden="true"></div>

<!-- VQ-25: обёртка для скопинга стилей полей ввода (owner 2026-09-14). -->
<div class="vq25-compare-root">
	<AnswerCompare />
</div>
