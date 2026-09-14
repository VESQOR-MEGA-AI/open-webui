# Facts

## Technical
- Админ-доступ реализован через `get_admin_user` (`backend/open_webui/utils/auth.py:516`) [source: PLAN-RU.md §2, date: 2026-09-12]
- Админ-раздел фронта: `src/routes/(app)/admin/` (табы Users / Evaluations / Functions), лейаут редиректит не-админов; стиль-образец `VesqorAdmin.svelte` [source: PLAN-RU.md §2, date: 2026-09-12]
- Мост к движку VESQOR: `routers/vesqor.py` — httpx-прокси на `VESQOR_API_BASE_URL` с `VESQOR_SERVICE_TOKEN`; при пустом токене — 503 not configured [source: PLAN-RU.md §2, date: 2026-09-12]
- OpenAI-совместимые провайдеры конфигурируются через `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS` (`config.py:329–341`) [source: PLAN-RU.md §2, date: 2026-09-12]
- Миграции — alembic, `run_migrations()` (`config.py:62`); фактическая голова **`v0e1s1q1r`** (62 версии, единственная голова, без разрывов down_revision) — подтверждено прогоном `test/test_vesqor_signup_gates.py` в стадии 0. PLAN-RU.md ошибочно называл `v2p1r0e0s0e0t` — не использовать [source: `test/test_vesqor_signup_gates.py` run output, date: 2026-09-12]
- Реальный CI-гейт для PR с базой `vesqor` — только `.github/workflows/vesqor-build.yml` (PR job: `python3 test/test_vesqor_signup_gates.py` + `vite build`); `backend.yaml`/`frontend.yaml` триггерятся только для `main`/`dev`, для нашего PR не применяются [source: чтение workflow-файлов, date: 2026-09-12]
- Origin репозитория на ветке `vesqor` получил ещё 3 коммита прода после снятого снапшота плана (не 6, как оценивалось): фича "Confidentiality/seal toggle" в композере чата (`0b644d344`, `0ea49b796`, `ec0b128b0`) — подтянуты fast-forward в стадии 0 без конфликтов [source: `git fetch`/`git log`, date: 2026-09-12]
- Фронт-примитивы для переиспользования: `Markdown.svelte` (безопасный рендер без исполнения HTML), `Spinner.svelte`, `ConfirmDialog.svelte`, toast `svelte-sonner`, i18n `t()` [source: PLAN-RU.md §2, date: 2026-09-12]
- Тесты: фронт — vitest (`npm run test:frontend`, есть `shortcuts.test.ts`); бэкенд — pytest (`test/test_vesqor_signup_gates.py`); CI бэкенда — `ruff format --check` + `ruff check --select=F`; CI ветки `vesqor` — `npm ci` + `vite build` + деплой Fly.io [source: PLAN-RU.md §2, date: 2026-09-12]
- Локальный git remote `origin` = `https://github.com/VESQOR-MEGA-AI/open-webui.git` (не `Tigr23239/open-webui`, как в PLAN-RU.md) [source: `git remote -v`, date: 2026-09-12]
- Локальные ветки на момент старта: `feat/vq25-answer-benchmark-2026-09-12` (текущая, с незакоммиченными изменениями), `vesqor` [source: `git branch -a`, date: 2026-09-12]
- В проекте нет CLAUDE.md (файл отсутствует на верхнем уровне репозитория) [source: `ls CLAUDE.md`, date: 2026-09-12]
- Рабочая директория при старте задачи уже содержит незакоммиченные изменения: modified `vite.config.ts`, untracked `backend/.install-deps.sh`, множество статических ассетов, `run-local.sh`, `vite.config.ts.bak` [source: `git status`, date: 2026-09-12]

## Business / Process
- Ключей провайдеров (OpenAI/Gemini/VESQOR) локально нет — везде честные состояния «требуется настройка», тестовые двойники только в тестах [source: SPEC.md, PLAN-RU.md §3, date: 2026-09-12]
- Git-флоу по плану: ветка → push в форк → PR с базой `vesqor` → ревью/мердж Сергей; пуш только по явному «пушь» [source: PLAN-RU.md шапка, date: 2026-09-12]
- Черновик ревизии 1 (в `vesqor-core`) отменён владельцем — переиспользуются только выверенные решения по схеме/критериям, не код [source: PLAN-RU.md шапка и §7, date: 2026-09-12]

## Гипотезы (НЕ проверенные факты — не повышать в статусе без подтверждения владельцем)
- **Движок VESQOR — OpenAI-совместимая «дверь»**: `POST /api/v1/chat/completions` + `GET /api/v1/models`, авторизация `Bearer <agent-token>` (`bs_live_…`), id моделей `vesqor-reasoning`/`vesqor-extraction`/`vesqor-classification`/`vesqor-default`; чат ходит в мозг, наставив на неё `OPENAI_API_BASE_URL`/`OPENAI_API_KEY`. **Источник — документ о намерении**: `docs/spec-vesqor-integration.md:12-18,34-36` описывает другой клон (`sergeyveys/open-webui`) на другом, bare-metal сервере. Текущий прод — Fly (`fly.toml`, app `vesqor-chat`), его секреты отсюда не видны; локально `OPENAI_*` не заданы вовсе и `.env` содержит `ENABLE_OPENAI_API=false`. Косвенное подтверждение — `routers/openai.py:1301-1308` (печать конфиденциальности вешается на общем OpenAI-пути, что верно для любого OpenAI-совместимого endpoint). **Проверить у владельца на стадии 7:** что лежит в `OPENAI_API_KEY` на Fly и какой адрес двери живой. Решение #009 принято по асимметрии риска, а не по подтверждению [source: разведка стадии 2, date: 2026-09-12]

## Gate Baseline (Stage 0, 2026-09-12) — pre-existing, not to be fixed as part of VQ-25
- `ruff format --check` — 🔴 15 pre-existing unformatted files (none touched by VQ-25 plan) [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `ruff check --select=F ...` (exact CI command) — 🟢 clean [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `test/test_vesqor_signup_gates.py`, `test/test_vesqor_seal_ui.py` — 🟢 both pass [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `svelte-check` — 🔴 pre-existing 8407 errors / 210 warnings / 356 files (baseline count to diff against later, not to fix) [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `eslint .` — 🔴 crashes entirely (pre-existing plugin bug in `@typescript-eslint/no-unused-vars` while linting `FilePreview.svelte`) — no report producible repo-wide; VQ-25's own new files may need to be linted directly instead [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `vitest --run` (CI=true) — 🟢 1 file / 2 tests pass. NB: `npm run test:frontend` without `--run` hangs in watch mode outside CI — always add `--run`/`CI=true` [source: SCRATCH.md Stage 0, date: 2026-09-12]
- `vite build` (`--max-old-space-size=4608`, pyodide already cached) — 🟢 builds clean in ~3m25s [source: SCRATCH.md Stage 0, date: 2026-09-12]
- Full baseline detail and file lists: see `/opt/work-archive/2026-09-12/vq25-answer-comparison/SCRATCH.md`
