"""
VESQOR three-assistant presets (2026-09-10).

Creates three workspace models on startup if they do not exist yet:

  - vesqor-copilot  -> "Copilot" style (concise, bullet recommendations)
  - vesqor-chatgpt  -> "ChatGPT" style (general-purpose, conversational)
  - vesqor-vesqor   -> "VESQOR MEGA AI" style (structured decision report)

All three are thin presets over the SAME base model that the chat already
uses (whatever the configured OpenAI-compatible provider exposes), mirroring
the live comparison block on vesqorai.com (app/api/comparison/live/route.ts
in vesqor-core). The system prompts are kept in sync with that route.

The presets are created as workspace models owned by the first admin user
(super admin). If no admin exists yet (fresh install before first signup),
the seed is skipped and will run on the next startup.

The seed must run AFTER the model pre-fetch (app.state.OPENAI_MODELS is
populated) so it can pick a real base model id from the connected provider.
"""

import logging

from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models
from open_webui.models.users import Users

log = logging.getLogger(__name__)

# Fallback base model id when the provider list is empty at seed time.
FALLBACK_BASE_MODEL_ID = "openai/gpt-4.1-mini"

# Model ids that are not suitable as a chat base model.
_SKIP_KEYWORDS = ("embedding", "image", "whisper", "tts", "rerank", "moderation")

ASSISTANT_PRESETS = [
    {
        "id": "vesqor-copilot",
        "name": "Copilot",
        "description": "Microsoft Copilot style — concise summary + 3-5 bullet recommendations.",
        "system": (
            "You are Microsoft Copilot, an AI assistant integrated into Microsoft 365. "
            "Answer the user's business question in a concise, practical style: a short "
            "summary followed by 3-5 bullet-point recommendations. Do not mention that you "
            "are an API integration."
        ),
    },
    {
        "id": "vesqor-chatgpt",
        "name": "ChatGPT",
        "description": "ChatGPT style — direct, conversational general-purpose answers.",
        "system": (
            "You are ChatGPT, a general-purpose AI assistant. Answer the user's business "
            "question directly and conversationally. Use short paragraphs and a few bullet "
            "points where helpful. Do not mention that you are an API integration."
        ),
    },
    {
        "id": "vesqor-vesqor",
        "name": "VESQOR",
        "description": "VESQOR MEGA AI style — structured decision report with confidence score.",
        "system": (
            "You are VESQOR MEGA AI, a governed business-analysis engine. Produce a "
            "structured decision report: \"Summary\" (2-3 sentences), \"Key findings\" "
            "(3-5 bullets), \"Recommendations\" (3-5 numbered items), and \"Confidence\" "
            "(a score 0-100 with a one-line rationale). Use markdown headings and lists. "
            "Do not mention that you are an API integration."
        ),
    },
]


def _pick_base_model_id(app) -> str | None:
    """Pick the first chat-capable model id from the pre-fetched provider list."""
    models = getattr(getattr(app, "state", None), "OPENAI_MODELS", None)
    if not models:
        return None
    data = models.get("data", []) if isinstance(models, dict) else models
    for model in data:
        model_id = model.get("id") or model.get("name") or ""
        if any(k in model_id.lower() for k in _SKIP_KEYWORDS):
            continue
        return model_id
    return None


async def seed_three_assistants(app=None) -> None:
    """Idempotent: create the three preset models if they are missing."""
    try:
        admin = await Users.get_super_admin_user()
        if admin is None:
            log.info("seed_three_assistants: no admin user yet, skipping")
            return

        base_model_id = _pick_base_model_id(app) if app is not None else None
        if base_model_id is None:
            base_model_id = FALLBACK_BASE_MODEL_ID
            log.warning(
                "seed_three_assistants: no provider models available, using fallback %s",
                base_model_id,
            )

        for preset in ASSISTANT_PRESETS:
            existing = await Models.get_model_by_id(preset["id"])
            if existing:
                continue

            form = ModelForm(
                id=preset["id"],
                base_model_id=base_model_id,
                name=preset["name"],
                meta=ModelMeta(description=preset["description"]),
                params=ModelParams.model_validate({"system": preset["system"]}),
                is_active=True,
            )
            created = await Models.insert_new_model(form, admin.id)
            if created:
                log.info("seed_three_assistants: created %s (base %s)", preset["id"], base_model_id)
            else:
                log.warning("seed_three_assistants: failed to create %s", preset["id"])
    except Exception as e:
        log.exception("seed_three_assistants failed: %s", e)
