# SPEC: VESQOR MEGA AI — Open WebUI fork as main chat UI

## Objective
Fork Open WebUI (already forked: `sergeyveys/open-webui`, cloned at
`/home/claudeproxy/projects/open-webui`) and turn it into the **VESQOR MEGA AI**
chat interface — the user-facing front-end for the VESQOR brain
(OpenAI-compatible door at `POST /api/v1/chat/completions`).

## Context (verified facts)
- Upstream: https://github.com/open-webui/open-webui (SvelteKit frontend + FastAPI backend).
- Fork: https://github.com/sergeyveys/open-webui (clone HEAD `01f4282f1`, ~516MB).
- Integration seam: `backend/open_webui/config.py:320` —
  `OPENAI_API_BASE_URL` env (default https://api.openai.com/v1) + `OPENAI_API_KEY`.
  Point these at the VESQOR door and the chat UI talks to the VESQOR brain
  (auth → rate limit → shield → recall → route → model → validate → remember).
- VESQOR door: OpenAI-compatible, `POST /api/v1/chat/completions` + `GET /api/v1/models`.
  Auth: `Authorization: Bearer <agent-token>` (bs_live_…). Model ids: vesqor-reasoning /
  vesqor-extraction / vesqor-classification / vesqor-default (router keys).
- Branding: "VESQOR MEGA AI" — NEVER mention AI providers/models/'model' in copy.
  All UI copy must use "VESQOR MEGA AI".
- Server: 154.59.110.75, no Docker (disk 80% = 5.8GB free), node v22, npm 10,
  python 3.12, nginx on 80/443. Deploy will be bare-metal (uvicorn + npm build + nginx),
  NOT docker-compose.
- Claude Code runs via OpenRouter bridge (http://127.0.0.1:8899, ANTHROPIC_AUTH_TOKEN
  from OPENROUTER_API_KEY in /root/.hermes/.env) — top-up may be needed mid-run.
  Use `--model claude-fable-5 --max-turns 60 --permission-mode dontAsk
  --allowedTools "Bash,Read,Edit,Write,Glob,Grep,TodoWrite,WebSearch,WebFetch"`.

## Deliverables
1. **Branding pass** — replace "Open WebUI"/"OpenWebUI" with "VESQOR MEGA AI"
   across user-facing surfaces (login page title/logo, sidebar, header, about page,
   page <title>). Keep internal code identifiers/class names untouched.
2. **Default connection** — VESQOR door as the default/only connection:
   - Set `OPENAI_API_BASE_URL` to the VESQOR door URL (production: brain-shield.vercel.app
     or vesqor.com — verify which is live; .env.deploy will hold the value).
   - `OPENAI_API_KEY` = agent token (bs_live_…, value in .env.deploy, chmod 600).
   - Disable/`ENABLE_OLLAMA_API=false`, hide unused connections (Ollama) from UI if cheap.
3. **Production deploy on bare metal** (no Docker):
   - Backend: python venv + uv/poetry install, uvicorn on 127.0.0.1:8081, systemd unit.
   - Frontend: npm run build → serve via nginx (path / or /chat), proxy /api → uvicorn.
   - nginx site config: `chat.sslip.io` (or path /chat on existing 154-59-110-75.sslip.io —
     pick the simpler; server already serves AIFittingRoom on /).
   - Data dir for SQLite/state under /var/lib/open-webui/ (or project dir), backup-able.
4. **Auth** — single admin user (email sergey.veys@gmail.com), password in .env.deploy.
   Optionally `WEBUI_AUTH=false` for open access on first deploy (chat-only), revisit later.
5. **Verification (run all, fix failures)**:
   - `npm run build` (frontend) — must succeed.
   - `pytest backend/tests` (backend) — at least the chat/models smoke tests.
   - Boot uvicorn, curl `GET /api/health`, then a chat round-trip through
     `POST /api/chat/completions` with the VESQOR model id — must return a real
     VESQOR answer (x-brain-source in vq_meta if exposed).
   - Load the site in browser: login, send a message, see a VESQOR answer.
6. **Docs** — `docs/open-webui-vesqor.md`: runbook (start/stop/backup/update),
   env vars, how to point at a different VESQOR door, known limits.

## Constraints
- Do NOT touch upstream git history; work on a `vesqor` branch off the clone HEAD.
- Do NOT commit secrets (agent token, passwords) — they live in `.env.deploy` (chmod 600,
  gitignored).
- Keep changes minimal & mergeable upstream: branding + env wiring + deploy config.
- Branding: VESQOR MEGA AI everywhere user-facing; NO model/provider names in copy.
- Do NOT commit, do NOT deploy to production — leave work in the working tree,
  report what you changed + verification output. Hermes commits and deploys.

## Steps (suggested)
1. Recon: read backend/open_webui/config.py env block, src/lib (i18n strings for
   "Open WebUI"), +layout.svelte, login page. Find all user-facing brand strings.
2. Branding pass (frontend i18n + static + <title> + logo text).
3. Env wiring: confirm OPENAI_API_BASE_URL/KEY path works end-to-end (mock door first
   if VESQOR unreachable: a tiny OpenAI-compatible stub returning a canned answer).
4. Bare-metal deploy: venv, uvicorn 8081, npm build, nginx site, systemd unit.
5. Verification suite (above). Fix failures.
6. Write docs/open-webui-vesqor.md.
Report: files changed, verification results (build/test/curl/browser), deploy plan for Hermes.
