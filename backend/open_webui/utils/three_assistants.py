"""VESQOR assistant style presets.

Creates three workspace models on startup if they do not already exist:

  - vesqor-copilot  -> concise, practical "office assistant" style
  - vesqor-chatgpt  -> direct, conversational general-purpose style
  - vesqor-vesqor   -> VESQOR MEGA AI structured decision report style

All three are thin presets over the SAME base model the chat already uses
(the configured OpenAI-compatible provider), mirroring the comparison block
on vesqorai.com (apps/vesqor-mega-ai/app/api/comparison/live/route.ts in
vesqor-core), which is the single source of truth for these styles.

Two correctness notes (both were defects in the original proposal, PR #3):

1. ACCESS GRANTS. A workspace model with no grants is invisible to every
   non-admin: check_model_access() in utils/access_control raises
   HTTPException(403, 'Model not found') when the caller has no read grant on
   the model AND no read grant on its base model. This exact failure took the
   chat down for the `user` role on 2026-08-26. The seed therefore grants
   `user:* read` on each preset it creates.

2. FALLBACK MODEL ID. The fallback must be a model the provider actually
   serves, otherwise the preset points at a non-existent base and every
   request 404s. The provider's real ids are vesqor-* (vesqor-reasoning is
   first, and is registered with a public grant), so that is the fallback.

Prompts describe a STYLE and never assert another company's identity: a
preset must not make the model claim "I am Microsoft Copilot" or "I am
ChatGPT" inside our own product. Each prompt carries an explicit guard.

Idempotent: an existing preset is left untouched, so a restart never
overwrites a model an admin has since edited.
"""

import logging

from open_webui.models.access_grants import AccessGrants
from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models
from open_webui.models.users import Users

log = logging.getLogger(__name__)

# Fallback base model id when the provider list is empty at seed time.
# Must be an id the provider actually serves (see module docstring, note 2).
FALLBACK_BASE_MODEL_ID = 'vesqor-reasoning'

# Public read grant applied to every preset, so non-admin roles can use them.
PUBLIC_READ_GRANT = [
    {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
]

# Model ids that are not suitable as a chat base model.
_SKIP_KEYWORDS = ('embedding', 'image', 'whisper', 'tts', 'rerank', 'moderation')

# Shared guard: the preset must answer in a style, not impersonate a vendor.
_NO_IMPERSONATION = (
    'Answer in the style described above. Never claim to be, or speak as, '
    "another company's product or assistant."
)

ASSISTANT_PRESETS = [
    {
        'id': 'vesqor-copilot',
        'name': 'Copilot',
        'description': (
            'Concise office-assistant style — short summary plus 3-5 bullet recommendations.'
        ),
        'system': (
            'Answer the user\'s business question in a concise, practical style: '
            'a short summary followed by 3-5 bullet-point recommendations. '
            + _NO_IMPERSONATION
        ),
    },
    {
        'id': 'vesqor-chatgpt',
        'name': 'ChatGPT',
        'description': (
            'Direct conversational style — general-purpose answers in short paragraphs.'
        ),
        'system': (
            'Answer the user\'s business question directly and conversationally. '
            'Use short paragraphs and a few bullet points where helpful. '
            + _NO_IMPERSONATION
        ),
    },
    {
        'id': 'vesqor-vesqor',
        'name': 'VESQOR',
        'description': (
            'VESQOR MEGA AI style — structured decision report with confidence score.'
        ),
        'system': (
            'You are VESQOR MEGA AI, a governed business-analysis engine. Produce a '
            'structured decision report: "Summary" (2-3 sentences), "Key findings" '
            '(3-5 bullets), "Recommendations" (3-5 numbered items), and "Confidence" '
            '(a score 0-100 with a one-line rationale). Use markdown headings and lists. '
            'Do not mention that you are an API integration.'
        ),
    },
]


def _pick_base_model_id(app) -> str | None:
    """Pick the first chat-capable model id from the pre-fetched provider list."""
    models = getattr(getattr(app, 'state', None), 'OPENAI_MODELS', None)
    if not models:
        return None
    data = models.get('data', []) if isinstance(models, dict) else models
    for model in data:
        model_id = model.get('id') or model.get('name') or ''
        if any(k in model_id.lower() for k in _SKIP_KEYWORDS):
            continue
        return model_id
    return None


async def seed_three_assistants(app=None) -> None:
    """Idempotent: create the three preset models if they are missing."""
    try:
        admin = await Users.get_super_admin_user()
        if admin is None:
            log.info('seed_three_assistants: no admin user yet, skipping')
            return

        base_model_id = _pick_base_model_id(app) if app is not None else None
        if base_model_id is None:
            base_model_id = FALLBACK_BASE_MODEL_ID
            log.warning(
                'seed_three_assistants: no provider models available, using fallback %s',
                base_model_id,
            )

        for preset in ASSISTANT_PRESETS:
            existing = await Models.get_model_by_id(preset['id'])
            if existing:
                continue

            form = ModelForm(
                id=preset['id'],
                base_model_id=base_model_id,
                name=preset['name'],
                meta=ModelMeta(description=preset['description']),
                params=ModelParams.model_validate({'system': preset['system']}),
                access_grants=PUBLIC_READ_GRANT,
                is_active=True,
            )
            created = await Models.insert_new_model(form, admin.id)
            if created:
                # insert_new_model already applies form.access_grants; re-assert
                # so a variant that skips them can never leave the preset
                # invisible to non-admins (the 2026-08-26 failure mode).
                await AccessGrants.set_access_grants(
                    'model', preset['id'], PUBLIC_READ_GRANT
                )
                log.info(
                    'seed_three_assistants: created %s (base %s)', preset['id'], base_model_id
                )
            else:
                log.warning('seed_three_assistants: failed to create %s', preset['id'])
    except Exception as e:
        log.exception('seed_three_assistants failed: %s', e)
