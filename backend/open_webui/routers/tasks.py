import asyncio
import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from open_webui.config import (
    DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
)
from open_webui.constants import ERROR_MESSAGES, TASKS
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.memories import Memories
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.payload import apply_params_to_form_data
from open_webui.utils.task import (
    autocomplete_generation_template,
    emoji_generation_template,
    follow_up_generation_template,
    get_task_model_id,
    image_prompt_generation_template,
    moa_response_generation_template,
    query_generation_template,
    tags_generation_template,
    title_generation_template,
)
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

SUGGESTIONS_CACHE_TTL_SECONDS = 15 * 60
SUGGESTIONS_GENERATION_TIMEOUT_SECONDS = 90
SUGGESTIONS_COUNT = 4
SUGGESTIONS_MAX_CHAT_TITLES = 10
SUGGESTIONS_MAX_MEMORIES = 10

# Module-level, per-user cache. Fine for a single uvicorn worker process;
# each worker regenerates its own copy independently.
_suggestions_cache: dict[str, tuple[float, list[dict]]] = {}

TASK_CONFIG_KEYS = {
    'TASK_MODEL': 'task.model.default',
    'TASK_MODEL_EXTERNAL': 'task.model.external',
    'TASK_MODEL_PARAMS': 'task.model.params',
    'TITLE_GENERATION_PROMPT_TEMPLATE': 'task.title.prompt_template',
    'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE': 'task.image.prompt_template',
    'ENABLE_AUTOCOMPLETE_GENERATION': 'task.autocomplete.enable',
    'AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH': 'task.autocomplete.input_max_length',
    'AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE': 'task.autocomplete.prompt_template',
    'TAGS_GENERATION_PROMPT_TEMPLATE': 'task.tags.prompt_template',
    'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE': 'task.follow_up.prompt_template',
    'ENABLE_FOLLOW_UP_GENERATION': 'task.follow_up.enable',
    'ENABLE_TAGS_GENERATION': 'task.tags.enable',
    'ENABLE_TITLE_GENERATION': 'task.title.enable',
    'ENABLE_SEARCH_QUERY_GENERATION': 'task.query.search.enable',
    'ENABLE_RETRIEVAL_QUERY_GENERATION': 'task.query.retrieval.enable',
    'QUERY_GENERATION_PROMPT_TEMPLATE': 'task.query.prompt_template',
    'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE': 'task.tools.prompt_template',
    'ENABLE_VOICE_MODE_PROMPT': 'task.voice.prompt.enable',
    'VOICE_MODE_PROMPT_TEMPLATE': 'task.voice.prompt_template',
}


async def get_config_values(key_map: dict[str, str]) -> dict:
    values = await Config.get_many(*key_map.values())
    return {field: values[storage_key] for field, storage_key in key_map.items() if storage_key in values}


def config_updates(data: dict, key_map: dict[str, str]) -> dict:
    return {key_map[field]: value for field, value in data.items() if field in key_map}


def apply_task_model_params(payload: dict, models: dict, task_model_id: str, params: dict | None = None) -> dict:
    model = models.get(payload.get('model')) or models.get(task_model_id)
    if not model or (not params and not payload.get('params')):
        return payload
    return apply_params_to_form_data(payload, model, params or None)


async def get_task_model_generation_config(default_model_id: str, models) -> tuple[str, dict]:
    config = await Config.get_many(
        'task.model.default',
        'task.model.external',
        'task.model.params',
    )
    params = config.get('task.model.params') or {}
    if not isinstance(params, dict):
        params = {}

    return (
        get_task_model_id(
            default_model_id,
            config.get('task.model.default'),
            config.get('task.model.external'),
            models,
        ),
        {key: value for key, value in params.items() if value is not None and value != ''},
    )


##################################
#
# Task Endpoints
#
##################################


@router.get('/config')
async def get_task_config(request: Request, user=Depends(get_verified_user)):
    return await get_config_values(TASK_CONFIG_KEYS)


class TaskConfigForm(BaseModel):
    TASK_MODEL: Optional[str]
    TASK_MODEL_EXTERNAL: Optional[str]
    TASK_MODEL_PARAMS: dict | None = None
    ENABLE_TITLE_GENERATION: bool
    TITLE_GENERATION_PROMPT_TEMPLATE: str
    IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_AUTOCOMPLETE_GENERATION: bool
    AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: int
    AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE: str
    TAGS_GENERATION_PROMPT_TEMPLATE: str
    FOLLOW_UP_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_FOLLOW_UP_GENERATION: bool
    ENABLE_TAGS_GENERATION: bool
    ENABLE_SEARCH_QUERY_GENERATION: bool
    ENABLE_RETRIEVAL_QUERY_GENERATION: bool
    QUERY_GENERATION_PROMPT_TEMPLATE: str
    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE: str
    ENABLE_VOICE_MODE_PROMPT: bool
    VOICE_MODE_PROMPT_TEMPLATE: Optional[str]


@router.post('/config/update')
async def update_task_config(request: Request, form_data: TaskConfigForm, user=Depends(get_admin_user)):
    await Config.upsert(config_updates(form_data.model_dump(), TASK_CONFIG_KEYS))
    return await get_config_values(TASK_CONFIG_KEYS)


@router.post('/title/completions')
async def generate_title(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not await Config.get('task.title.enable'):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Title generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='No model specified for title generation. Please ensure a model is selected for this chat.',
        )
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating chat title using model %s for user %s ', task_model_id, user.email)

    title_template = await Config.get('task.title.prompt_template')
    if title_template != '':
        template = title_template
    else:
        template = DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE

    content = await title_generation_template(template, form_data['messages'], user)
    task_model_params = task_model_params or {
        'max_tokens': models[task_model_id].get('info', {}).get('params', {}).get('max_tokens', 1000)
    }

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TITLE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/follow_up/completions')
async def generate_follow_ups(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not await Config.get('task.follow_up.enable'):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Follow-up generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating chat title using model %s for user %s ', task_model_id, user.email)

    follow_up_template = await Config.get('task.follow_up.prompt_template')
    if follow_up_template != '':
        template = follow_up_template
    else:
        template = DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE

    content = await follow_up_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.FOLLOW_UP_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/tags/completions')
async def generate_chat_tags(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not await Config.get('task.tags.enable'):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Tags generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating chat tags using model %s for user %s ', task_model_id, user.email)

    tags_template = await Config.get('task.tags.prompt_template')
    if tags_template != '':
        template = tags_template
    else:
        template = DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE

    content = await tags_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TAGS_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/image_prompt/completions')
async def generate_image_prompt(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating image prompt using model %s for user %s ', task_model_id, user.email)

    image_prompt_template = await Config.get('task.image.prompt_template')
    if image_prompt_template != '':
        template = image_prompt_template
    else:
        template = DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    content = await image_prompt_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.IMAGE_PROMPT_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/queries/completions')
async def generate_queries(request: Request, form_data: dict, user=Depends(get_verified_user)):
    type = form_data.get('type')
    if type == 'web_search':
        if not await Config.get('task.query.search.enable'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.FEATURE_DISABLED('Search query generation'),
            )
    elif type == 'retrieval':
        if not await Config.get('task.query.retrieval.enable'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.FEATURE_DISABLED('Query generation'),
            )

    if getattr(request.state, 'cached_queries', None):
        log.info('Reusing cached queries: %s', request.state.cached_queries)
        return request.state.cached_queries

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating %s queries using model %s for user %s', type, task_model_id, user.email)

    query_template = await Config.get('task.query.prompt_template')
    if query_template.strip() != '':
        template = query_template
    else:
        template = DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE

    content = await query_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.QUERY_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/auto/completions')
async def generate_autocompletion(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not await Config.get('task.autocomplete.enable'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.FEATURE_DISABLED('Autocompletion generation'),
        )

    type = form_data.get('type')
    prompt = form_data.get('prompt')
    messages = form_data.get('messages')

    autocomplete_input_max_length = await Config.get('task.autocomplete.input_max_length')
    if autocomplete_input_max_length > 0:
        if len(prompt) > autocomplete_input_max_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.INPUT_TOO_LONG(autocomplete_input_max_length),
            )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, task_model_params = await get_task_model_generation_config(model_id, models)

    log.debug('generating autocompletion using model %s for user %s', task_model_id, user.email)

    autocomplete_template = await Config.get('task.autocomplete.prompt_template')
    if autocomplete_template.strip() != '':
        template = autocomplete_template
    else:
        template = DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE

    content = await autocomplete_generation_template(template, prompt, messages, type, user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.AUTOCOMPLETE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, task_model_params)

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/emoji/completions')
async def generate_emoji(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    task_model_id, _ = await get_task_model_generation_config(model_id, models)

    log.debug('generating emoji using model %s for user %s ', task_model_id, user.email)

    template = DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE

    content = await emoji_generation_template(template, form_data['prompt'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.EMOJI_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    payload = apply_task_model_params(payload, models, task_model_id, {'max_tokens': 4})

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/moa/completions')
async def generate_moa_response(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']

    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(),
        )

    template = DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE

    content = moa_response_generation_template(
        template,
        form_data['prompt'],
        form_data['responses'],
    )

    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': form_data.get('stream', False),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'chat_id': form_data.get('chat_id', None),
            'task': str(TASKS.MOA_RESPONSE_GENERATION),
            'task_body': form_data,
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


def build_smart_suggestions_prompt(chat_titles: list[str], memory_contents: list[str]) -> str:
    context_sections = []
    if chat_titles:
        context_sections.append(
            'Recent conversation topics (most recent first):\n'
            + '\n'.join(f'- {title}' for title in chat_titles)
        )
    if memory_contents:
        context_sections.append(
            'Known facts about the user:\n' + '\n'.join(f'- {content}' for content in memory_contents)
        )

    context = '\n\n'.join(context_sections) if context_sections else 'No prior context is available for this user.'

    return (
        'A business user has been working with us. Their recent conversation topics and known preferences are ground truth.\n'
        'The task: propose the four most useful follow-up questions this user could ask next, grounded in that history\n'
        '(if the history is thin, propose generally useful starter questions instead of inventing facts).\n'
        'Do not mention any AI model, vendor, or provider name. paraphrase, never quote the data verbatim.\n\n'
        f'{context}\n\n'
        'FORMAT (strict): a numbered list of exactly 4 items. Each item is one line:\n'
        '1. <short title, max 4 words> | <short continuation> || <full ready-to-send prompt message>\n'
        'No preamble, no explanation, just the 4 numbered lines.'
    )


def parse_smart_suggestions(raw: str) -> list[dict]:
    """Parse the model's answer into suggestion cards.

    Accepts either a JSON {"suggestions": [...]} payload (legacy) or the
    strict number lines format the task prompt asks for:
        1. <title> | <continuation> || <full prompt>
    """
    text = (raw or '').strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            items = parsed.get('suggestions') if isinstance(parsed, dict) else None
            if isinstance(items, list):
                suggestions = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = item.get('title')
                    content = item.get('content')
                    if not isinstance(title, list) or len(title) != 2:
                        continue
                    if not all(isinstance(part, str) and part.strip() for part in title):
                        continue
                    if not isinstance(content, str) or not content.strip():
                        continue
                    suggestions.append({'title': [part.strip() for part in title], 'content': content.strip()})
                    if len(suggestions) == SUGGESTIONS_COUNT:
                        break
                if suggestions:
                    return suggestions
        except ValueError:
            pass

    # Line-based fallback (the format the task prompt asks for).
    suggestions = []
    for line in text.splitlines():
        line = line.strip()
        if not line or '|' not in line:
            continue
        # Strip a leading "1. " numbering if present.
        line = re.sub(r'^\d+\.\s*', '', line)
        parts = [part.strip() for part in line.split('|')]
        if len(parts) < 2:
            continue
        title_parts = parts[0].split('||')
        headline = title_parts[0].strip()
        content = parts[-1].strip()
        # Remove the ||-joined full prompt from the tail if it leaked there.
        if '||' in parts[-1]:
            tailParts = parts[-1].split('||')
            content = tailParts[-1].strip()
        continuation = ''
        if len(parts) >= 3:
            continuation = parts[1].strip()
        elif len(title_parts) > 1:
            continuation = title_parts[1].strip()
        if not headline or not content:
            continue
        suggestions.append({'title': [headline, continuation or ''], 'content': content})
        if len(suggestions) == SUGGESTIONS_COUNT:
            break
    return suggestions


@router.get('/suggestions')
async def get_smart_suggestions(request: Request, user=Depends(get_verified_user)):
    if not await Config.get('task.suggestions.enable', True):
        return []

    cached = _suggestions_cache.get(user.id)
    if cached and (time.time() - cached[0]) < SUGGESTIONS_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        models = request.app.state.MODELS
        default_model_id = next(iter(models), None)
        if not default_model_id:
            return []

        task_model_id, task_model_params = await get_task_model_generation_config(default_model_id, models)

        chats = await Chats.get_chat_list_by_user_id(user.id, limit=SUGGESTIONS_MAX_CHAT_TITLES)
        chat_titles = [chat.title for chat in chats if chat.title]

        memories = await Memories.get_memories_by_user_id(user.id) or []
        memories = sorted(memories, key=lambda memory: memory.updated_at, reverse=True)[:SUGGESTIONS_MAX_MEMORIES]
        memory_contents = [memory.content for memory in memories if memory.content]

        content = build_smart_suggestions_prompt(chat_titles, memory_contents)

        payload = {
            'model': task_model_id,
            'messages': [{'role': 'user', 'content': content}],
            'stream': False,
            'metadata': {
                **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
                'task': str(TASKS.SUGGESTIONS_GENERATION),
                'chat_id': None,
            },
        }

        payload = await process_pipeline_inlet_filter(request, payload, user, models)
        payload = apply_task_model_params(
            payload, models, task_model_id,
            {**(task_model_params or {}), **{'max_tokens': 1024}}
        )

        response = await asyncio.wait_for(
            generate_chat_completion(request, form_data=payload, user=user),
            timeout=SUGGESTIONS_GENERATION_TIMEOUT_SECONDS,
        )

        raw = response['choices'][0]['message']['content']
        suggestions = parse_smart_suggestions(raw)
        if not suggestions:
            log.info('Smart suggestions parse-empty; raw head: %r', (raw or '')[:200])
    except Exception:
        log.debug('Smart suggestions generation failed for user %s', user.id, exc_info=True)
        return []

    if suggestions:
        _suggestions_cache[user.id] = (time.time(), suggestions)
    return suggestions
