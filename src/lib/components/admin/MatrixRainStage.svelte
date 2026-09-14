<script lang="ts">
	/**
	 * MatrixRainStage — живой VESQOR Matrix rain (native WebGL2) для админ-страниц.
	 *
	 * Прямой порт ядра matrix-rain-background.tsx (лендинг vesqorai.com,
	 * ветка feature/matrix-rain-2026-09-10, 2026-09-11) из React в Svelte 5.
	 * Вырезано всё, что привязано к лендингу:
	 *   - scroll-зоны (inactiveEnd 792/activeEnd 1400) — здесь дождь идёт ВСЕГДА,
	 *     по всему вьюпорту, без слов и без «decoding»-состояния;
	 *   - word sprites + MSDF-слова — на админ-странице не нужны;
	 *   - document-координаты → экранные (scrollY = 0 всегда).
	 * Оставлено НЕприкосновенным: шейдеры, конфиг глифов/слоёв, инстансинг,
	 * MSDF-атлас (/matrix/font-atlas.{json,png}), trail-decay, low-power, DPR-cap.
	 *
	 * Прогрессивное улучшение: WebGL2 → WebGL1+ANGLE → ничего (страница просто
	 * без фона). aria-hidden + pointer-events:none, чисто декоративно.
	 */

	import { onMount, onDestroy } from 'svelte';

	/* ================= 1. CONFIG ================= */

	const CONFIG = {
		colors: {
			brightest: '#eafff2',
			bright: '#7ef0b0',
			primary: '#39b54a',
			muted: '#1f7a3d',
			halo: '#7ef0b0'
		},
		msdf: {
			jsonUrl: '/matrix/font-atlas.json',
			baseUrl: '/matrix/',
			capCentreRatio: 0.365,
			outlineEm: 0.095,
			haloEm: 0.15,
			outlineAlpha: 0,
			outlineAlphaSoft: 0,
			haloAlpha: 0
		},
		glyphs: {
			binary: '01',
			letters: 'AIVESQORMGDNTLCBUP',
			exotic: 'kxznvwyjqftbdhl',
			binaryBias: 0.68,
			letterBias: 0.9
		},
		streams: {
			countWide: 140,
			countLowPower: 80,
			countNarrow: 60,
			centreShare: 0.3,
			centreAlpha: 0.5,
			layers: [
				{ key: 'bg' as const, weight: 0.44, fontSize: 11, speedMin: 26, speedMax: 46, alpha: 0.34, trail: 12 },
				{ key: 'mid' as const, weight: 0.34, fontSize: 15, speedMin: 52, speedMax: 84, alpha: 0.58, trail: 15 },
				{ key: 'fg' as const, weight: 0.22, fontSize: 21, speedMin: 92, speedMax: 140, alpha: 0.92, trail: 18 }
			],
			cellRatio: 1.18,
			mutationChancePerSecond: 0.9,
			trailDecayPower: 1.5,
			scrollSpeedBoost: 2.5,
			headCount: 3,
			headBoost: 1.6,
			headAlphaMax: 0.95
		},
		zones: {
			edgePx: 240,
			edgeFracMax: 0.3,
			narrowEdgeFrac: 0.34,
			narrowBreakpoint: 640
		},
		perf: {
			dprCap: 1.75,
			dprCapLowPower: 1.25,
			maxFrameSeconds: 0.05,
			resizeHeightTolerancePx: 120,
			maxInstances: 20000
		}
	} as const;

	/* ================= 2. Types ================= */

	type LayerKey = 'bg' | 'mid' | 'fg';
	const LAYER_ORDER: readonly LayerKey[] = ['bg', 'mid', 'fg'];

	interface Stream {
		layer: LayerKey;
		x: number;
		docY: number;
		speed: number;
		fontSize: number;
		cell: number;
		trail: number;
		alpha: number;
		glyphs: string[];
		lastCellDocY: number;
	}

	interface Viewport {
		width: number;
		height: number;
		narrow: boolean;
	}

	interface Glyph {
		px0: number;
		py0: number;
		pw: number;
		ph: number;
		u0: number;
		v0: number;
		u1: number;
		v1: number;
		advance: number;
		drawable: boolean;
	}

	interface Atlas {
		glyphs: Map<string, Glyph>;
		image: HTMLImageElement;
		rangeRatio: number;
	}

	type Rgb = readonly [number, number, number];

	/* ================= 3. Atlas loader ================= */

	interface BmChar {
		char?: string;
		id: number;
		x: number;
		y: number;
		width: number;
		height: number;
		xoffset: number;
		yoffset: number;
		xadvance: number;
	}

	interface BmFont {
		info: { size: number };
		common: { base: number; scaleW: number; scaleH: number };
		pages: string[];
		chars: BmChar[];
		distanceField?: { fieldType: string; distanceRange: number };
	}

	function loadImage(url: string): Promise<HTMLImageElement> {
		return new Promise((resolve, reject) => {
			const image = new Image();
			image.crossOrigin = 'anonymous';
			image.onload = () => resolve(image);
			image.onerror = () => reject(new Error(`matrix atlas: cannot load ${url}`));
			image.src = url;
		});
	}

	async function loadAtlas(signal: AbortSignal): Promise<Atlas> {
		const response = await fetch(CONFIG.msdf.jsonUrl, { signal, cache: 'force-cache' });
		if (!response.ok) throw new Error(`matrix atlas: ${response.status} for ${CONFIG.msdf.jsonUrl}`);
		const font = (await response.json()) as BmFont;

		const size = font.info.size;
		const base = font.common.base;
		const { scaleW, scaleH } = font.common;
		const range = font.distanceField?.distanceRange ?? 4;

		const glyphs = new Map<string, Glyph>();
		for (const ch of font.chars) {
			const key = ch.char ?? String.fromCharCode(ch.id);
			glyphs.set(key, {
				px0: (ch.xoffset - ch.xadvance / 2) / size,
				py0: (ch.yoffset - base) / size + CONFIG.msdf.capCentreRatio,
				pw: ch.width / size,
				ph: ch.height / size,
				u0: ch.x / scaleW,
				v0: ch.y / scaleH,
				u1: (ch.x + ch.width) / scaleW,
				v1: (ch.y + ch.height) / scaleH,
				advance: ch.xadvance / size,
				drawable: ch.width > 0 && ch.height > 0
			});
		}

		const page = font.pages[0];
		if (!page) throw new Error('matrix atlas: no page in font json');
		const image = await loadImage(CONFIG.msdf.baseUrl + page);

		return { glyphs, image, rangeRatio: range / size };
	}

	/* ================= 4. Maths + capability ================= */

	interface NavigatorWithMemory extends Navigator {
		deviceMemory?: number;
	}

	function detectLowPower(): boolean {
		if (typeof navigator === 'undefined') return true;
		const nav = navigator as NavigatorWithMemory;
		if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency <= 4) return true;
		if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 4) return true;
		return /Android|iPhone|iPad|iPod|Mobile|Silk|Opera Mini/i.test(nav.userAgent ?? '');
	}

	function prefersReducedMotion(): boolean {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
		return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	}

	function clamp(value: number, min: number, max: number): number {
		return value < min ? min : value > max ? max : value;
	}

	function lerp(from: number, to: number, t: number): number {
		return from + (to - from) * t;
	}

	function randomBetween(min: number, max: number): number {
		return min + Math.random() * (max - min);
	}

	function hexToRgb(hex: string): Rgb {
		const n = parseInt(hex.slice(1), 16);
		return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
	}

	const RGB = {
		brightest: hexToRgb(CONFIG.colors.brightest),
		bright: hexToRgb(CONFIG.colors.bright),
		primary: hexToRgb(CONFIG.colors.primary),
		muted: hexToRgb(CONFIG.colors.muted),
		halo: hexToRgb(CONFIG.colors.halo)
	} as const;

	function pickGlyph(): string {
		const roll = Math.random();
		const set =
			roll < CONFIG.glyphs.binaryBias
				? CONFIG.glyphs.binary
				: roll < CONFIG.glyphs.letterBias
					? CONFIG.glyphs.letters
					: CONFIG.glyphs.exotic;
		return set[Math.floor(Math.random() * set.length)];
	}

	function readViewport(): Viewport {
		const width = window.innerWidth;
		return {
			width,
			height: window.innerHeight,
			narrow: width < CONFIG.zones.narrowBreakpoint
		};
	}

	/* ================= 5. Streams ================= */

	function pickColumnX(view: Viewport, inCentre: boolean): number {
		const band = view.narrow
			? view.width * CONFIG.zones.narrowEdgeFrac
			: Math.min(CONFIG.zones.edgePx, view.width * CONFIG.zones.edgeFracMax);

		if (inCentre) {
			const centreWidth = Math.max(0, view.width - band * 2);
			return band + Math.random() * centreWidth;
		}
		return Math.random() < 0.5 ? Math.random() * band : view.width - Math.random() * band;
	}

	function createStream(view: Viewport, layerIndex: number, inCentre: boolean, scale: number): Stream {
		const layer = CONFIG.streams.layers[layerIndex];
		const fontSize = Math.max(8, Math.round(layer.fontSize * scale));
		const cell = fontSize * CONFIG.streams.cellRatio;
		const trail = Math.max(5, Math.round(layer.trail * (inCentre ? 0.55 : 1)));
		// Экранные координаты, scrollY = 0: колонки сразу раскиданы по всей
		// высоте вьюпорта (плюс хвост над верхней кромкой).
		const docY =
			(trail + 2) * cell + Math.random() * (view.height + view.height * 0.6);

		return {
			layer: layer.key,
			x: pickColumnX(view, inCentre),
			docY,
			speed: randomBetween(layer.speedMin, layer.speedMax) * scale,
			fontSize,
			cell,
			trail,
			alpha: layer.alpha * (inCentre ? CONFIG.streams.centreAlpha : 1),
			glyphs: Array.from({ length: trail }, pickGlyph),
			lastCellDocY: docY
		};
	}

	function pickLayerIndex(): number {
		const roll = Math.random();
		let cumulative = 0;
		for (let i = 0; i < CONFIG.streams.layers.length; i += 1) {
			cumulative += CONFIG.streams.layers[i].weight;
			if (roll < cumulative) return i;
		}
		return CONFIG.streams.layers.length - 1;
	}

	function buildStreams(view: Viewport, lowPower: boolean): Stream[] {
		const budget = view.narrow
			? CONFIG.streams.countNarrow
			: lowPower
				? CONFIG.streams.countLowPower
				: CONFIG.streams.countWide;

		const scale = view.narrow ? 0.82 : 1;
		const centreCount = Math.round(budget * CONFIG.streams.centreShare);
		const streams: Stream[] = [];

		for (let i = 0; i < budget; i += 1) {
			const inCentre = i < centreCount;
			const layerIndex = inCentre ? 0 : pickLayerIndex();
			streams.push(createStream(view, layerIndex, inCentre, scale));
		}
		streams.sort((a, b) => LAYER_ORDER.indexOf(a.layer) - LAYER_ORDER.indexOf(b.layer));
		return streams;
	}

	function recycleStream(stream: Stream, view: Viewport, scrollY: number): void {
		const above = scrollY - randomBetween(0, view.height * 0.6) - stream.trail * stream.cell;
		stream.docY = above;
		stream.lastCellDocY = stream.docY;
	}

	function advanceStream(stream: Stream, dt: number, speedScale: number, view: Viewport, scrollY: number): void {
		stream.docY += stream.speed * speedScale * dt;

		while (stream.docY - stream.lastCellDocY >= stream.cell) {
			stream.lastCellDocY += stream.cell;
			stream.glyphs.pop();
			stream.glyphs.unshift(pickGlyph());
		}

		if (Math.random() < CONFIG.streams.mutationChancePerSecond * dt) {
			const index = Math.floor(Math.random() * stream.glyphs.length);
			stream.glyphs[index] = pickGlyph();
		}

		if (stream.docY - stream.trail * stream.cell > scrollY + view.height) {
			recycleStream(stream, view, scrollY);
		}
	}

	/* ================= 6. GL ================= */

	const ATTRIB = {
		quad: 0,
		anchor: 1,
		scale: 2,
		plane: 3,
		uv: 4,
		fill: 5,
		alpha: 6,
		style: 7
	} as const;

	const FLOATS_PER_INSTANCE = 17;
	const INSTANCE_STRIDE = FLOATS_PER_INSTANCE * 4;

	function vertexSource(gl2: boolean): string {
		const IN = gl2 ? 'in' : 'attribute';
		const OUT = gl2 ? 'out' : 'varying';
		return `${gl2 ? '#version 300 es\n' : ''}precision highp float;

${IN} vec2 a_quad;
${IN} vec2 a_anchor;
${IN} float a_scale;
${IN} vec4 a_plane;
${IN} vec4 a_uv;
${IN} vec3 a_fill;
${IN} float a_alpha;
${IN} vec2 a_style;

uniform vec2 uResolution;
uniform float uScrollY;
uniform float uRangeRatio;
uniform vec2 uEdges;

${OUT} vec2 vUv;
${OUT} vec3 vFill;
${OUT} float vAlpha;
${OUT} vec2 vStyle;
${OUT} float vPxRange;
${OUT} vec2 vEdgePx;

void main() {
  vec2 local = a_plane.xy + a_quad * a_plane.zw;
  float x = a_anchor.x + local.x * a_scale;
  float y = (a_anchor.y - uScrollY) + local.y * a_scale;
  gl_Position = vec4((x / uResolution.x) * 2.0 - 1.0, 1.0 - (y / uResolution.y) * 2.0, 0.0, 1.0);

  vUv = mix(a_uv.xy, a_uv.zw, a_quad);
  vFill = a_fill;
  vAlpha = a_alpha;
  vStyle = a_style;
  vPxRange = max(a_scale * uRangeRatio, 0.0001);
  vEdgePx = uEdges * a_scale;
}
`;
	}

	function fragmentSource(gl2: boolean): string {
		const IN = gl2 ? 'in' : 'varying';
		const TEX = gl2 ? 'texture' : 'texture2D';
		const OUTDECL = gl2 ? 'out vec4 fragColor;' : '';
		const OUT = gl2 ? 'fragColor' : 'gl_FragColor';
		const PRECISION = gl2
			? 'precision highp float;'
			: `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif`;
		return `${gl2 ? '#version 300 es\n' : ''}${PRECISION}

${IN} vec2 vUv;
${IN} vec3 vFill;
${IN} float vAlpha;
${IN} vec2 vStyle;
${IN} float vPxRange;
${IN} vec2 vEdgePx;

uniform sampler2D uAtlas;
uniform vec3 uRingColor;
${OUTDECL}

float median(vec3 c) {
  return max(min(c.r, c.g), min(max(c.r, c.g), c.b));
}

void main() {
  float d = (median(${TEX}(uAtlas, vUv).rgb) - 0.5) * vPxRange;

  float fillA = clamp(d + 0.5, 0.0, 1.0);
  float outA = clamp(d + vEdgePx.x + 0.5, 0.0, 1.0) * vStyle.x;
  float haloA = clamp(d + vEdgePx.y + 0.5, 0.0, 1.0) * vStyle.y;

  float under = outA + (1.0 - outA) * haloA;
  float a = fillA + (1.0 - fillA) * under;
  vec3 rgb = vFill * fillA + uRingColor * (1.0 - fillA) * under;

  a *= vAlpha;
  if (a <= 0.002) discard;
  ${OUT} = vec4(rgb * vAlpha, a);
}
`;
	}

	type AnyGl = WebGLRenderingContext | WebGL2RenderingContext;

	interface Renderer {
		gl: AnyGl;
		program: WebGLProgram;
		vertexShader: WebGLShader;
		fragmentShader: WebGLShader;
		quadBuffer: WebGLBuffer;
		instanceBuffer: WebGLBuffer;
		texture: WebGLTexture;
		vao: WebGLVertexArrayObject | null;
		angle: ANGLE_instanced_arrays | null;
		uniforms: {
			resolution: WebGLUniformLocation | null;
			scrollY: WebGLUniformLocation | null;
			rangeRatio: WebGLUniformLocation | null;
			edges: WebGLUniformLocation | null;
			atlas: WebGLUniformLocation | null;
			ringColor: WebGLUniformLocation | null;
		};
		data: Float32Array;
		capacity: number;
		drawInstanced: (count: number) => void;
		bindAttributes: () => void;
	}

	function compile(gl: AnyGl, type: number, source: string): WebGLShader {
		const shader = gl.createShader(type);
		if (!shader) throw new Error('matrix gl: createShader failed');
		gl.shaderSource(shader, source);
		gl.compileShader(shader);
		if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
			const log = gl.getShaderInfoLog(shader);
			gl.deleteShader(shader);
			throw new Error(`matrix gl: shader compile failed — ${log}`);
		}
		return shader;
	}

	function acquireContext(canvas: HTMLCanvasElement): { gl: AnyGl; gl2: boolean } | null {
		const attributes: WebGLContextAttributes = {
			alpha: true,
			antialias: false,
			depth: false,
			stencil: false,
			premultipliedAlpha: true,
			preserveDrawingBuffer: false,
			powerPreference: 'low-power'
		};

		const gl2 = canvas.getContext('webgl2', attributes);
		if (gl2) return { gl: gl2, gl2: true };
		const gl1 = canvas.getContext('webgl', attributes);
		if (gl1) return { gl: gl1, gl2: false };
		return null;
	}

	function createRenderer(canvas: HTMLCanvasElement, atlas: Atlas, capacity: number): Renderer | null {
		const acquired = acquireContext(canvas);
		if (!acquired) return null;
		const { gl, gl2 } = acquired;

		const angle = gl2 ? null : (gl.getExtension('ANGLE_instanced_arrays') as ANGLE_instanced_arrays | null);
		if (!gl2 && !angle) return null;

		const vertexShader = compile(gl, gl.VERTEX_SHADER, vertexSource(gl2));
		const fragmentShader = compile(gl, gl.FRAGMENT_SHADER, fragmentSource(gl2));
		const program = gl.createProgram();
		if (!program) return null;

		gl.attachShader(program, vertexShader);
		gl.attachShader(program, fragmentShader);
		for (const [name, location] of Object.entries(ATTRIB)) {
			gl.bindAttribLocation(program, location, `a_${name}`);
		}
		gl.linkProgram(program);
		if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
			const log = gl.getProgramInfoLog(program);
			gl.deleteProgram(program);
			gl.deleteShader(vertexShader);
			gl.deleteShader(fragmentShader);
			throw new Error(`matrix gl: link failed — ${log}`);
		}

		const quadBuffer = gl.createBuffer();
		const instanceBuffer = gl.createBuffer();
		const texture = gl.createTexture();
		if (!quadBuffer || !instanceBuffer || !texture) return null;

		gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
		gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);

		const data = new Float32Array(capacity * FLOATS_PER_INSTANCE);
		gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
		gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);

		const gl2ctx = gl2 ? (gl as WebGL2RenderingContext) : null;
		const divisor = (location: number, value: number) => {
			if (gl2ctx) gl2ctx.vertexAttribDivisor(location, value);
			else angle!.vertexAttribDivisorANGLE(location, value);
		};

		function bindAttributes(): void {
			gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer!);
			gl.enableVertexAttribArray(ATTRIB.quad);
			gl.vertexAttribPointer(ATTRIB.quad, 2, gl.FLOAT, false, 0, 0);
			divisor(ATTRIB.quad, 0);

			gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer!);
			const attr = (location: number, size: number, offsetFloats: number) => {
				gl.enableVertexAttribArray(location);
				gl.vertexAttribPointer(location, size, gl.FLOAT, false, INSTANCE_STRIDE, offsetFloats * 4);
				divisor(location, 1);
			};
			attr(ATTRIB.anchor, 2, 0);
			attr(ATTRIB.scale, 1, 2);
			attr(ATTRIB.plane, 4, 3);
			attr(ATTRIB.uv, 4, 7);
			attr(ATTRIB.fill, 3, 11);
			attr(ATTRIB.alpha, 1, 14);
			attr(ATTRIB.style, 2, 15);
		}

		let vao: WebGLVertexArrayObject | null = null;
		if (gl2ctx) {
			vao = gl2ctx.createVertexArray();
			gl2ctx.bindVertexArray(vao);
			bindAttributes();
			gl2ctx.bindVertexArray(null);
		} else {
			bindAttributes();
		}

		gl.bindTexture(gl.TEXTURE_2D, texture);
		gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
		gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
		gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, atlas.image);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

		gl.disable(gl.DEPTH_TEST);
		gl.disable(gl.CULL_FACE);
		gl.enable(gl.BLEND);
		gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
		gl.clearColor(0, 0, 0, 0);

		gl.useProgram(program);
		const uniforms = {
			resolution: gl.getUniformLocation(program, 'uResolution'),
			scrollY: gl.getUniformLocation(program, 'uScrollY'),
			rangeRatio: gl.getUniformLocation(program, 'uRangeRatio'),
			edges: gl.getUniformLocation(program, 'uEdges'),
			atlas: gl.getUniformLocation(program, 'uAtlas'),
			ringColor: gl.getUniformLocation(program, 'uRingColor')
		};
		gl.uniform1i(uniforms.atlas, 0);
		gl.uniform1f(uniforms.rangeRatio, atlas.rangeRatio);
		gl.uniform2f(uniforms.edges, CONFIG.msdf.outlineEm, CONFIG.msdf.haloEm);
		gl.uniform3f(uniforms.ringColor, RGB.halo[0], RGB.halo[1], RGB.halo[2]);

		const drawInstanced = (count: number) => {
			if (gl2ctx) gl2ctx.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, count);
			else angle!.drawArraysInstancedANGLE(gl.TRIANGLE_STRIP, 0, 4, count);
		};

		return {
			gl,
			program,
			vertexShader,
			fragmentShader,
			quadBuffer,
			instanceBuffer,
			texture,
			vao,
			angle,
			uniforms,
			data,
			capacity,
			drawInstanced,
			bindAttributes
		};
	}

	function destroyRenderer(renderer: Renderer): void {
		const { gl } = renderer;
		gl.useProgram(null);
		gl.bindBuffer(gl.ARRAY_BUFFER, null);
		gl.bindTexture(gl.TEXTURE_2D, null);
		if (renderer.vao && 'bindVertexArray' in gl) {
			(gl as WebGL2RenderingContext).bindVertexArray(null);
			(gl as WebGL2RenderingContext).deleteVertexArray(renderer.vao);
		}
		gl.deleteBuffer(renderer.quadBuffer);
		gl.deleteBuffer(renderer.instanceBuffer);
		gl.deleteTexture(renderer.texture);
		gl.deleteProgram(renderer.program);
		gl.deleteShader(renderer.vertexShader);
		gl.deleteShader(renderer.fragmentShader);
		gl.getExtension('WEBGL_lose_context')?.loseContext();
	}

	/* ================= 7. Frame assembly ================= */

	function pushGlyph(
		data: Float32Array,
		count: number,
		capacity: number,
		atlas: Atlas,
		char: string,
		x: number,
		docY: number,
		scale: number,
		fill: Rgb,
		alpha: number,
		outline: number,
		halo: number
	): number {
		if (count >= capacity || alpha <= 0.004) return count;
		const glyph = atlas.glyphs.get(char);
		if (!glyph || !glyph.drawable) return count;

		let o = count * FLOATS_PER_INSTANCE;
		data[o] = x;
		data[o + 1] = docY;
		data[o + 2] = scale;
		data[o + 3] = glyph.px0;
		data[o + 4] = glyph.py0;
		data[o + 5] = glyph.pw;
		data[o + 6] = glyph.ph;
		data[o + 7] = glyph.u0;
		data[o + 8] = glyph.v0;
		data[o + 9] = glyph.u1;
		data[o + 10] = glyph.v1;
		data[o + 11] = fill[0];
		data[o + 12] = fill[1];
		data[o + 13] = fill[2];
		data[o + 14] = alpha;
		data[o + 15] = outline;
		data[o + 16] = halo;
		o += FLOATS_PER_INSTANCE;
		return count + 1;
	}

	function pushStream(
		data: Float32Array,
		count: number,
		capacity: number,
		atlas: Atlas,
		stream: Stream,
		view: Viewport,
		scrollY: number,
		intensity: number
	): number {
		const headScreenY = stream.docY - scrollY;
		if (headScreenY < -stream.cell || headScreenY - stream.trail * stream.cell > view.height + stream.cell) {
			return count;
		}

		const n = stream.glyphs.length;
		const soft = stream.layer === 'bg';
		const outline = soft ? CONFIG.msdf.outlineAlphaSoft : CONFIG.msdf.outlineAlpha;

		for (let i = 0; i < n; i += 1) {
			const docY = stream.docY - i * stream.cell;
			const screenY = docY - scrollY;
			if (screenY < -stream.cell || screenY > view.height + stream.cell) continue;

			const decay = Math.pow(1 - i / n, CONFIG.streams.trailDecayPower);
			const head = i < CONFIG.streams.headCount;
			const raw = stream.alpha * decay * intensity;
			const alpha = head ? Math.min(CONFIG.streams.headAlphaMax, raw * CONFIG.streams.headBoost) : raw;
			const fill = i === 0 ? RGB.brightest : head ? RGB.bright : i < n * 0.6 ? RGB.primary : RGB.muted;

			count = pushGlyph(
				data,
				count,
				capacity,
				atlas,
				stream.glyphs[i],
				stream.x,
				docY,
				stream.fontSize,
				fill,
				alpha,
				outline * Math.min(1, alpha * 2.2),
				0
			);
		}
		return count;
	}

	/* ================= 8. Scene ================= */

	interface Scene {
		stop: () => void;
		pause: () => void;
		resume: () => void;
	}

	function startScene(canvas: HTMLCanvasElement, reduced: boolean): Scene {
		const lowPower = detectLowPower();
		const dprCap = lowPower ? CONFIG.perf.dprCapLowPower : CONFIG.perf.dprCap;

		let view = readViewport();
		let streams: Stream[] = [];
		let scrollY = 0;
		let rafId = 0;
		let staticRaf = 0;
		let resizeRaf = 0;
		let lastTs = 0;
		let running = false;
		let stopped = false;

		let atlas: Atlas | null = null;
		let renderer: Renderer | null = null;
		const abort = new AbortController();

		function resizeCanvas(): void {
			const dpr = Math.min(window.devicePixelRatio || 1, dprCap);
			canvas.width = Math.max(1, Math.round(view.width * dpr));
			canvas.height = Math.max(1, Math.round(view.height * dpr));
			if (!renderer) return;
			renderer.gl.viewport(0, 0, canvas.width, canvas.height);
			renderer.gl.uniform2f(renderer.uniforms.resolution, view.width, view.height);
		}

		function rebuild(): void {
			view = readViewport();
			resizeCanvas();
			streams = buildStreams(view, lowPower);
		}

		function render(intensity: number): void {
			if (!renderer || !atlas) return;
			const { gl, data, capacity } = renderer;

			gl.clearColor(0, 0, 0, 0);
			gl.clear(gl.COLOR_BUFFER_BIT);

			let count = 0;
			for (const stream of streams) {
				count = pushStream(data, count, capacity, atlas, stream, view, scrollY, intensity);
			}
			if (count === 0) return;

			gl.useProgram(renderer.program);
			if (renderer.vao) (gl as WebGL2RenderingContext).bindVertexArray(renderer.vao);
			gl.activeTexture(gl.TEXTURE0);
			gl.bindTexture(gl.TEXTURE_2D, renderer.texture);
			gl.uniform1f(renderer.uniforms.scrollY, scrollY);

			gl.bindBuffer(gl.ARRAY_BUFFER, renderer.instanceBuffer);
			gl.bufferSubData(gl.ARRAY_BUFFER, 0, data.subarray(0, count * FLOATS_PER_INSTANCE));
			renderer.drawInstanced(count);
		}

		function drawStaticFrame(): void {
			render(0.55);
		}

		function frame(ts: number): void {
			rafId = window.requestAnimationFrame(frame);
			if (!lastTs) lastTs = ts;
			const dt = Math.min((ts - lastTs) / 1000, CONFIG.perf.maxFrameSeconds);
			lastTs = ts;

			// Полная интенсивность всегда: админ-страница — не лендинг, дождь идёт сразу.
			const speedScale = 1 + CONFIG.streams.scrollSpeedBoost;
			for (const stream of streams) {
				advanceStream(stream, dt, speedScale, view, scrollY);
			}

			render(1);
		}

		function play(): void {
			if (running || reduced || stopped || !renderer) return;
			running = true;
			lastTs = 0;
			rafId = window.requestAnimationFrame(frame);
		}

		function pause(): void {
			if (!running) return;
			running = false;
			window.cancelAnimationFrame(rafId);
			rafId = 0;
		}

		function onStaticScroll(): void {
			if (staticRaf) return;
			staticRaf = window.requestAnimationFrame(() => {
				staticRaf = 0;
				drawStaticFrame();
			});
		}

		function onVisibility(): void {
			if (document.hidden) {
				pause();
			} else {
				play();
			}
		}

		function onResize(): void {
			if (resizeRaf) return;
			resizeRaf = window.requestAnimationFrame(() => {
				resizeRaf = 0;
				const next = readViewport();
				const widthChanged = next.width !== view.width;
				const heightChanged = Math.abs(next.height - view.height) > CONFIG.perf.resizeHeightTolerancePx;
				if (!widthChanged && !heightChanged) return;
				rebuild();
				if (reduced) drawStaticFrame();
			});
		}

		function onContextLost(event: Event): void {
			event.preventDefault();
			pause();
			renderer = null;
		}

		function onContextRestored(): void {
			if (stopped || !atlas) return;
			renderer = createRenderer(canvas, atlas, CONFIG.perf.maxInstances);
			if (!renderer) return;
			rebuild();
			if (reduced) drawStaticFrame();
			else play();
		}

		canvas.addEventListener('webglcontextlost', onContextLost);
		canvas.addEventListener('webglcontextrestored', onContextRestored);

		scrollY = 0;
		window.scrollTo(0, window.scrollY); // не трогаем реальный скролл — просто фиксируем anchor

		loadAtlas(abort.signal)
			.then((loaded) => {
				if (stopped) return;
				atlas = loaded;
				renderer = createRenderer(canvas, loaded, CONFIG.perf.maxInstances);
				if (!renderer) return;

				rebuild();

				if (reduced) {
					drawStaticFrame();
					window.addEventListener('scroll', onStaticScroll, { passive: true });
					window.addEventListener('resize', onResize, { passive: true });
					return;
				}

				window.addEventListener('resize', onResize, { passive: true });
				document.addEventListener('visibilitychange', onVisibility);
				if (!document.hidden) play();
			})
			.catch(() => {
				// Атлас недоступен/офлайн — страница работает без фона.
			});

		return {
			stop: () => {
				stopped = true;
				abort.abort();
				pause();
				if (staticRaf) window.cancelAnimationFrame(staticRaf);
				if (resizeRaf) window.cancelAnimationFrame(resizeRaf);
				window.removeEventListener('scroll', onStaticScroll);
				window.removeEventListener('resize', onResize);
				document.removeEventListener('visibilitychange', onVisibility);
				canvas.removeEventListener('webglcontextlost', onContextLost);
				canvas.removeEventListener('webglcontextrestored', onContextRestored);
				if (renderer) {
					destroyRenderer(renderer);
					renderer = null;
				}
				atlas = null;
			},
			pause,
			resume: play
		};
	}

	/* ================= 9. Svelte shell ================= */

	let canvasEl: HTMLCanvasElement;
	let scene: Scene | null = null;

	onMount(() => {
		if (!canvasEl) return;
		document.body.classList.add('vesqor-matrix-live');

		let currentScene = startScene(canvasEl, prefersReducedMotion());

		// VQ-25: дождь стоит на паузе, пока пользователь работает в чате.
		// app.html вешает body.vesqor-active на ЛЮБУЮ активность (клик,
		// скролл, клавишу, фокус во ввод) и снимает через 2 минуты
		// бездействия. Пауза = полный стоп анимации (rAF отменяется),
		// resume с ровного места. Раздельно от vesqor-interacting (6с,
		// матовое стекло): дождь молчит, пока пользователь занят.
		const activityObserver = new MutationObserver(() => {
			const active = document.body.classList.contains('vesqor-active');
			if (active) {
				currentScene.pause();
			} else {
				currentScene.resume();
			}
		});
		activityObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });

		const mq =
			typeof window.matchMedia === 'function'
				? window.matchMedia('(prefers-reduced-motion: reduce)')
				: null;

		const onPreferenceChange = () => {
			currentScene.stop();
			currentScene = startScene(canvasEl, prefersReducedMotion());
		};
		mq?.addEventListener('change', onPreferenceChange);

		scene = {
			stop: () => {
				mq?.removeEventListener('change', onPreferenceChange);
				activityObserver.disconnect();
				currentScene.stop();
			}
		};
	});

	onDestroy(() => {
		scene?.stop();
		scene = null;
		document.body.classList.remove('vesqor-matrix-live');
	});
</script>

<div class="matrix-stage-page-bg" aria-hidden="true"></div>
<canvas
	bind:this={canvasEl}
	class="matrix-stage-bg"
	aria-hidden="true"
></canvas>

<style>
	/* Тёмная подложка (брендовый лес) — слой под canvas. Canvas чистится в полную
	   прозрачность каждый кадр, поэтому собственную подложку он нести не может. */
	.matrix-stage-page-bg {
		position: fixed;
		inset: 0;
		z-index: 0;
		background: #0b1f15;
		pointer-events: none;
	}

	.matrix-stage-bg {
		position: fixed;
		inset: 0;
		z-index: 0;
		width: 100%;
		height: 100%;
		display: block;
		pointer-events: none;
	}

	@media (prefers-reduced-motion: reduce) {
		.matrix-stage-bg {
			animation: none;
		}
	}
</style>
