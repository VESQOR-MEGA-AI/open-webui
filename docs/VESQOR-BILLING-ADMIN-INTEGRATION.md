# VESQOR Billing + Admin Integration into Open WebUI (2026-08-20)

Owner-approved: **chat.vesqorai.com (Open WebUI fork) is the ONLY UI** for the
VESQOR product. The brain (api.vesqorai.com) is a pure API engine. This spec
adds the VESQOR billing surface (user settings) and VESQOR admin surface
(admin settings) to the Open WebUI fork, proxying server-to-server to the
brain with a service agent-token.

## Architecture

```
Browser (chat.vesqorai.com)
  └─ Svelte components (SettingsModal tabs)
       └─ /api/v1/vesqor/*  (NEW FastAPI router in open-webui backend)
            └─ server-to-server → https://api.vesqorai.com/api/*  (brain)
                 headers: Authorization: Bearer <VESQOR_SERVICE_TOKEN>
                          X-VESQOR-User-Email: <user.email>
```

- The brain keeps ALL business logic (Stripe, credits, providers, tokens).
- Open WebUI is a thin proxy + UI. It never stores billing data.
- The service token is `VESQOR_SERVICE_TOKEN` env secret (already set on
  vesqor-chat Fly app). Brain base URL is `VESQOR_API_BASE_URL` (already set).
- The brain's `resolveServiceUser` (lib/auth/service-user.ts) authenticates
  the token and resolves/creates the Prisma user by `X-VESQOR-User-Email`.

## Backend (Python, FastAPI)

### 1. New router: `backend/open_webui/routers/vesqor.py`

A thin reverse proxy. All routes require `get_verified_user` (Open WebUI
session). The router forwards to the brain and injects:

```python
headers = {
    "Authorization": f"Bearer {VESQOR_SERVICE_TOKEN}",
    "X-VESQOR-User-Email": user.email,
    "Content-Type": "application/json",
}
```

Routes (mirror the brain's API surface):

| Method | Path | Brain target | Purpose |
|--------|------|--------------|---------|
| GET | `/api/v1/vesqor/billing/status` | `/api/billing/status` | subscription + credits + quota |
| POST | `/api/v1/vesqor/checkout` | `/api/checkout` | create Stripe checkout (variant in body) |
| POST | `/api/v1/vesqor/portal` | `/api/portal` | Stripe customer portal URL |
| GET | `/api/v1/vesqor/credits` | `/api/credits` | credit balance + transactions |
| POST | `/api/v1/vesqor/credits/topup` | `/api/credits/topup` | create credit topup checkout |
| GET | `/api/v1/vesqor/admin/providers` | `/api/admin/providers` | provider pool list |
| POST | `/api/v1/vesqor/admin/providers` | `/api/admin/providers` | add provider |
| PATCH | `/api/v1/vesqor/admin/providers/{id}` | `/api/admin/providers/{id}` | update provider |
| DELETE | `/api/v1/vesqor/admin/providers/{id}` | `/api/admin/providers/{id}` | delete provider |
| POST | `/api/v1/vesqor/admin/providers/{id}/test` | `/api/admin/providers/{id}/test` | test provider |
| GET | `/api/v1/vesqor/admin/tokens` | `/api/admin/tokens` | agent tokens list |
| POST | `/api/v1/vesqor/admin/tokens` | `/api/admin/tokens` | create token |
| PATCH | `/api/v1/vesqor/admin/tokens/{id}` | `/api/admin/tokens/{id}` | enable/disable token |
| DELETE | `/api/v1/vesqor/admin/tokens/{id}` | `/api/admin/tokens/{id}` | delete token |

Implementation notes:
- Use `httpx` (already a dependency) with `AsyncClient`.
- Forward the request body verbatim (JSON) for POST/PATCH.
- Return the brain's JSON response with its status code.
- On brain 401/403 → return 403 (the Open WebUI user is not a VESQOR admin).
- On brain 5xx → return 502 with a friendly message.
- Admin routes: only allow when `user.role == 'admin'` in Open WebUI
  (defense in depth — the brain also checks ADMIN_EMAILS).
- Register the router in `backend/open_webui/main.py`:
  `app.include_router(vesqor.router, prefix='/api/v1/vesqor', tags=['vesqor'])`
- Config: read `VESQOR_SERVICE_TOKEN` and `VESQOR_API_BASE_URL` from env
  (add to `backend/open_webui/env.py` with sane defaults: empty token →
  router returns 503 "VESQOR integration not configured").

### 2. `backend/open_webui/env.py`

Add:
```python
VESQOR_SERVICE_TOKEN: str = os.environ.get("VESQOR_SERVICE_TOKEN", "")
VESQOR_API_BASE_URL: str = os.environ.get("VESQOR_API_BASE_URL", "https://api.vesqorai.com")
```

## Frontend (Svelte)

### 3. New API client: `src/lib/apis/vesqor/index.ts`

Functions mirroring the backend routes, each taking `token` (Open WebUI
session token) and calling `${WEBUI_API_BASE_URL}/vesqor/...`:

- `getVesqorBillingStatus(token)`
- `createVesqorCheckout(token, variant)` → `{ url }`
- `createVesqorPortalSession(token)` → `{ url }`
- `getVesqorCredits(token)`
- `createVesqorCreditTopup(token, credits)` → `{ url }`
- `getVesqorProviders(token)`, `addVesqorProvider(token, body)`,
  `updateVesqorProvider(token, id, body)`, `deleteVesqorProvider(token, id)`,
  `testVesqorProvider(token, id)`
- `getVesqorTokens(token)`, `createVesqorToken(token, body)`,
  `updateVesqorToken(token, id, body)`, `deleteVesqorToken(token, id)`

### 4. User settings tab: `src/lib/components/chat/SettingsModal.svelte`

Add a new personal tab `billing` (title "Billing", icon: credit-card,
keywords: billing, subscription, plan, credits, payment, stripe, upgrade).

Render `<VesqorBilling />` when `selectedTab === 'billing'`.

New component `src/lib/components/chat/Settings/VesqorBilling.svelte`:
- On mount: `getVesqorBillingStatus(localStorage.token)`.
- Shows: plan name (Trial / Monthly / Quarterly / Weekly / None), status
  (active/canceled), next billing date if available, credits balance.
- Buttons:
  - "Upgrade" → `createVesqorCheckout(token, 'quarterly')` → redirect to url
  - "Manage subscription" → `createVesqorPortalSession(token)` → redirect
  - "Buy credits" (50 / 150) → `createVesqorCreditTopup(token, n)` → redirect
- Loading state, error state (toast on failure).
- All copy in English (VESQOR product language; the fork's i18n is
  secondary — keep strings inline or add to en.json only).

### 5. Admin settings tab: `src/lib/components/chat/SettingsModal.svelte`

Add admin tab `admin:vesqor` (title "VESQOR", icon: brain/chip,
keywords: vesqor, brain, providers, tokens, api, keys, usage).

Render `<VesqorAdmin />` when `selectedTab === 'admin:vesqor'`.

New component `src/lib/components/admin/Settings/VesqorAdmin.svelte`:
- Two sub-sections (tabs or accordion):
  - **Providers**: list from `getVesqorProviders`; add form (name, baseUrl,
    apiKey, model, priority); enable/disable toggle; test button (shows
    ok/error); delete with confirm.
  - **Tokens**: list from `getVesqorTokens`; create form (label, owner,
    limits); show raw token ONCE in a copyable field after creation; disable/
    enable; delete with confirm.
- Only rendered for `$user.role === 'admin'` (the admin tab list is already
  admin-gated in SettingsModal).

### 6. Admin nav (optional but nice): `src/routes/(app)/admin/+layout.svelte`

Add a "VESQOR" link in the admin nav bar pointing to `/admin/settings/vesqor`
(consistent with the existing tab pattern). The settings tab list in
SettingsModal.svelte already handles `admin:vesqor` via the `adminSettings`
array — add the entry there.

## Verification

1. `python3 -m py_compile` on the new backend files (or run the backend
   tests if feasible — the fork's test suite is heavy; compile check is the
   minimum).
2. Frontend: `npx vite build` must succeed (CI does this on push to vesqor).
3. Manual E2E after deploy:
   - Sign in as admin (sergey.veys@gmail.com) → Settings → Billing tab shows
     status (no subscription yet → "No active plan").
   - Admin → Settings → VESQOR tab lists providers (empty) and tokens.
   - Create a token → raw token shown once.
   - Non-admin user sees Billing tab but NOT the VESQOR admin tab.

## Deploy

Push to `vesqor` branch → GitHub Actions `vesqor-build.yml` builds frontend
and deploys to Fly (vesqor-chat). Backend changes are baked into the Docker
image (Dockerfile copies the repo). No manual steps.

## Rollback

`git revert` the commit + push → CI redeploys the previous image. The brain
is untouched (no brain changes in this spec — the brain's service-user auth
was already deployed in commit d010ed9).
