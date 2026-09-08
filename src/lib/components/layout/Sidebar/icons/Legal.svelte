<script lang="ts">
	export let className = 'size-4';
	export let strokeWidth = '1.5';
</script>

<svg
	class={className}
	aria-hidden="true"
	xmlns="http://www.w3.org/2000/svg"
	viewBox="0 0 24 24"
	stroke-width={strokeWidth}
	fill="none"
>
	<!-- Scale of justice: balance beam + pans -->
	<path
		d="M12 3V21M8 21H16M12 3L4 8M12 3L20 8"
		stroke="currentColor"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
	<path
		d="M4 8H20"
		stroke="currentColor"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
	<path
		d="M4 8C4 8 2.5 9.5 2.5 11C2.5 12.3807 3.61929 13.5 5 13.5C6.38071 13.5 7.5 12.3807 7.5 11C7.5 9.5 6 8 6 8"
		stroke="currentColor"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
	<path
		d="M20 8C20 8 18.5 9.5 18.5 11C18.5 12.3807 19.6193 13.5 21 13.5C22.3807 13.5 23.5 12.3807 23.5 11C23.5 9.5 22 8 22 8"
		stroke="currentColor"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
</svg>
