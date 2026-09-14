# Project Profile

## Identity
- **Name**: vesqor-chat-dev (рабочая копия прод-чата chat.vesqorai.com)
- **Type**: SaaS — форк Open WebUI 0.11.0 (пакет `open-webui`), кастомизированный под VESQOR
- **Description**: Рабочая копия production-чата VESQOR (chat.vesqorai.com) на базе Open WebUI. Ветка `vesqor` синхронизирована с продом; поверх неё ведутся фичи. Текущая задача (тикет #VQ-25) — админ-страница сравнения качества ответов ChatGPT / Gemini / движка VESQOR со слепым LLM-судейством.
- **Started** (для этого репо-контекста памяти): 2026-09-12

## Goals
- **Primary**: Реализовать VQ-25 — admin-only страницу генерации и слепого сравнения ответов трёх систем (ChatGPT, Gemini, VESQOR engine) с судейством, сводкой, персистентностью и историей запусков.
- **Secondary**: Не сломать существующий прод-функционал (auth-гейты, миграции, UI-стили матрицы/стекла), гейты репозитория остаются зелёными.

## Stack & Tools
- Backend: Python (FastAPI, Open WebUI backend), SQLAlchemy + Alembic (`run_migrations()`, config.py:62), pytest, ruff (`ruff format --check`, `ruff check --select=F`)
- Frontend: SvelteKit, vitest (`npm run test:frontend`), i18n через `t()`, `svelte-sonner` для тостов
- Own engine bridge: `routers/vesqor.py` — httpx-прокси на `VESQOR_API_BASE_URL` + `VESQOR_SERVICE_TOKEN`, честный 503 при пустом токене
- Provider integration point: `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS` (config.py:329–341) — OpenAI-совместимый шов, планируется переиспользовать для ChatGPT и Gemini (через его OpenAI-совместимый endpoint)
- CI: бэкенд — ruff; фронт-ветка `vesqor` — `npm ci` + `vite build` + деплой на Fly.io
- Деплой: Fly.io

## Team & Roles
- **Владелец продукта**: Сергей (sergey.veys@gmail.com) — решает архитектуру, одобряет пуш/мердж
- **Hermes**: проверяет гейты после каждой стадии, держит живое превью и отчётность
- **Claude Code**: исполнитель по стадиям (эта сессия)
- Git-флоу: ветка → push в форк → PR с базой `vesqor` → ревью и мердж — Сергей. Без явного «пушь»/одобрения — не пушим.

## Constraints
- Ключей провайдеров (OpenAI/Gemini/VESQOR service token) локально нет → честные состояния «требуется настройка», без моков/плейсхолдеров в приложении (тестовые двойники — только в тестах)
- Не коммитить, не пушить, не оставлять dev-серверы в foreground без явного разрешения
- Миграции — только аддитивные, применяются только к локальной БД
- Не рефакторить чужие интеграции сверх необходимого для задачи
- Диск ограничен (~2.7 ГБ после чистки на момент ревизии 2 плана) — следить перед сборками

## Communication
- **Language**: Русский — вопросы, подтверждения, отчёты человеку. Английский — код, комментарии, промпты/спецификации для агентов.
- **Style**: Стадийная работа по плану (PLAN-RU.md §5, стадии 0→7), короткий отчёт (3–5 предложений) после каждой стадии, STOP до следующей стадии без подтверждения.

## Framework Config
- Active agents: `code`, `review`, `debug`, `devops`, `vibe-mentor` (dev/SaaS-проект)
- Источники истины для текущей задачи: `/opt/work-archive/2026-09-12/vq25-answer-comparison/SPEC.md`, `/opt/work-archive/2026-09-12/vq25-answer-comparison/PLAN-RU.md`, `/opt/work-archive/2026-09-12/vq25-answer-comparison/TASK-FOR-CLAUDE.md`
- Комплексность текущей задачи: 🔴 Complex (новая архитектура: миграция, новые модели, множественные интеграции провайдеров, судейская логика) — по PLAN-RU.md уже пройдены investigation + plan + ADR-уровня решения (см. DECISIONS.md); работа продолжается по стадиям с делегированием `code`/`review` и STOP-гейтами.
