# API_CONTRACT.md — Legal & Enterprise Documents HTTP surface

**Status:** binding v1.0 · **Date:** 2026-09-08 · **Author:** Hermes orchestrator (from master prompt §22 + IMPLEMENTATION_PLAN.md + generate.py output shape + vesqor-core API conventions)

This is THE contract. `LEGAL-3.2` (API implementation) and `LEGAL-3.3` (UI, builds against mocked responses) implement the same shapes below. No field naming drift between frontend and backend.

## Conventions

- **Base:** all routes under `/api/v1/legal/*` on the brain (api.vesqorai.com).
- **Auth:** `Authorization: Bearer <agent-token>` + `X-VESQOR-User-Email: <email>` (existing `resolveServiceUser` pattern). Fail-closed: no/invalid token → `401`.
- **Tenant isolation:** server derives scope from the authenticated identity. A caller can only ever see its own tenant's documents. Cross-tenant id → `404` (not 403, to disclose nothing).
- **Feature flag:** `LEGAL_DEPARTMENT_ENABLED=false` (default) → ALL routes return `404` and MCP tools are absent. No other behavior change.
- **Content type:** JSON everywhere except `compare` (multipart) and `render` with `format=docx|pdf` (binary file, `Content-Disposition: attachment`).
- **Responses are bare objects** (no `{data: ...}` wrapper) — matches existing brain API style.
- **Never in any response/request/log:** template body, secrets, other tenants' data.
- **Rate limit:** same per-token mechanism as existing brain routes.

## Shared types

```
state: "TEMPLATE" | "DRAFT" | "MISSING_INFORMATION" | "READY_FOR_REVIEW" | "APPROVED"
     | "READY_FOR_SIGNATURE" | "PARTIALLY_SIGNED" | "EXECUTED" | "SUPERSEDED"
     | "TERMINATED" | "ARCHIVED"

readiness: "ready_to_sign" | "needs_input" | "blocked" | "additional_compliance_review"

authority: {
  signatoryId: string,          // "sergey_veys" | "ty_solsbery" | ...
  name: string,
  title: string,
  code: "AUTHORIZED" | "SIGNATORY_AUTHORITY_NOT_ACTIVE" | "CEO_APPROVAL_REQUIRED"
      | "EXECUTIVE_LEGAL_REVIEW" | "AUTHORITY_NOT_VERIFIED",
  allowed: boolean,
  reason?: string,
  escalateTo?: string,          // e.g. "sergey_veys"
  alternativeSignatory?: { id: string, name: string, title: string },  // never auto-substituted
  requiresCounselReview?: boolean,
  restrictedHits?: string[]     // transaction types that triggered review
}

documentSummary: {
  documentId, templateId, templateVersion, title,
  counterpartyName: string | null,
  state, readiness, revisionNumber, updatedAt
}

documentFull: documentSummary & {
  counterparty: { name, entityType, jurisdiction?, address? } | null,
  effectiveDate: string | null,          // ISO date
  fieldValues: object,                   // onboarding profile object (may be partial)
  renderedContent?: string,              // markdown, only when rendered
  signatories: [{ id, name, title }],
  missingRequired: string[],             // dotted paths, e.g. "counterparty.legal.name"
  missingRecommended: string[],
  authority: authority | null,
  blockers: [{ code, message, field? }], // from last validate run
  materialModified: boolean,
  counselReviewRequired: boolean,
  createdAt, updatedAt
}
```

## Operations

### 1. List templates
`GET /api/v1/legal/templates`
→ `200 { templates: [{ templateId, code, title, category, stage, version, jurisdiction, requiredFields: string[], optionalFields: string[], allowedSignatories: [{ id, name, title }] }] }`
Never includes template bodies.

### 2. Create document
`POST /api/v1/legal/documents`
Request: `{ templateId: "01", counterparty?: { name?, entityType?, jurisdiction?, address? }, effectiveDate?: "2026-09-15", prefill?: object, signatoryId?: "sergey_veys" }`
→ `200 documentFull` (state `DRAFT`, revisionNumber 1, authority resolved for the requested signatory).

### 3. Populate fields (bulk)
`POST /api/v1/legal/documents/{id}/populate`
Request: `{ fields: { ...onboarding-profile } }`
→ `200 documentFull` (revisionNumber incremented; audit `document.populated`). Missing fields still listed in `missingRequired`.

### 4. Update / natural-language edit
`PATCH /api/v1/legal/documents/{id}`
Request: `{ instruction?: "Change the client to ABC Corp.", fieldValues?: { ... } }` (either, or both)
→ `200 documentFull` OR `422 { blockers: [{ code: "LOCKED_CLAUSE", message, field }], touched: "insurance" | "indemnity" | "governing_law" | "liability_cap" }` — locked-clause hits are NOT applied, returned for review routing. Language-level change sets `materialModified=true`, `counselReviewRequired=true`.

### 5. Validate
`POST /api/v1/legal/documents/{id}/validate`
→ `200 { documentId, valid: boolean, blockers: [{ code, message, field? }], missingRequired: string[], warnings: string[], readiness, authority }`
`blocked` / `additional_compliance_review` readiness is a hard gate — cannot validate to `ready_to_sign` past it.

### 6. Render / export
`POST /api/v1/legal/documents/{id}/render`
Request: `{ format?: "md" | "html" | "docx" | "pdf", signatureCopy?: boolean }`
- `md` → `200 { documentId, revisionNumber, markdown, contentType: "text/markdown" }`
- `html` → `200 { documentId, revisionNumber, html, contentType: "text/html" }`
- `docx`/`pdf` → `200` binary with `Content-Disposition: attachment; filename="VMA-01-NDA__<counterparty>.pdf"` (via existing lib/export)
- `signatureCopy: true` with unresolved placeholders → `422 { blockers: [...] }` (refused)
- Draft render (non-signatureCopy) → output watermarked "DRAFT — NOT FOR EXECUTION".

### 7. Get document
`GET /api/v1/legal/documents/{id}`
→ `200 documentFull` · cross-tenant → `404`.

### 8. Search
`GET /api/v1/legal/documents?q=&templateId=&state=&readiness=`
→ `200 { documents: [documentSummary ...] }` (light list — no fieldValues/renderedContent). Tenant-scoped only.

### 9. Compare (upload & review)
`POST /api/v1/legal/compare` — multipart: `file` (pdf|docx|md|txt, ≤10MB), `templateId?`
→ `200 { comparison: { added: [{ clause, detail }], removed: [...], changed: [...], partyChanges, governingLaw, confidentialityDuration, ipProvisions, aiDataClauses, liabilityTerms, signatoryDifferences, unusualObligations }, disclaimer: "Descriptive only. Not legal advice. No edits applied." }`

### 10. Create onboarding package
`POST /api/v1/legal/packages`
Request: `{ counterparty: { name, entityType, jurisdiction?, address? }, answers: { m365Integration: boolean, personalDataProcessing: boolean, pilot: boolean, productionServices: boolean, sla: boolean } }`
→ `200 { packageId, counterparty, state: "DRAFT", documents: [{ documentId, templateId, title, because: string, executionOrder }] }`
Rules (from schema/intent_routing.json): m365→06, personal data→04+13, pilot→02, production→03+07+08, sla→10, always→01 + internal 14. Blocked Doc 13 does NOT block the package.

### 11. Package status
`GET /api/v1/legal/packages/{id}`
→ `200 { packageId, state, documents: [{ documentId, templateId, title, state, readiness, blocked: boolean, blockedReason?: string }], overallReady: boolean }`

### 12. State transition
`POST /api/v1/legal/documents/{id}/state`
Request: `{ to: "READY_FOR_SIGNATURE" }`
→ `200 { documentId, from, to, allowed: true, revisionNumber }` ·
→ `422 { from, to, allowed: false, reason: "..." }` (e.g. missing fields, blocked policy gate, authority not active, EXECUTED immutability → amendments go through populate → new revision).
Audit `document.state_changed` on every transition.

### 13. Audit trail
`GET /api/v1/legal/documents/{id}/audit`
→ `200 { events: [{ eventType, actor, revisionNumber, source, details, createdAt }] }`

## MCP tools (same shapes, tool-call params = request JSON, result = response JSON)

`list_document_templates`, `create_document`, `populate_document`, `update_document`, `validate_document`, `render_document`, `get_document`, `search_documents`, `compare_documents` (base64 file param), `create_onboarding_package`, `get_package_status`. Not registered when flag is off.

## Errors

| Code | When |
|---|---|
| 401 | missing/invalid token |
| 403 | authenticated but not authorized for this scope |
| 404 | unknown id OR feature flag off (indistinguishable by design) |
| 422 | validation/state-machine/policy rejection — body carries itemized `blockers` |
| 400 | malformed request (bad templateId, bad format, oversized file) |
| 429 | rate limit |
