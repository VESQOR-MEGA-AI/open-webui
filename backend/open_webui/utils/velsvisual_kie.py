"""
velsvisual_kie — Python-порт клиента KIE API (kie.ai) для Open WebUI.
Портировано из https://github.com/nick-vels/VelsVisual (src/client.js, src/models.js,
src/registry.js, src/recommend.js, src/pricing.js) — MIT, Nick Vels.

Назначение: генерация фото/видео/аудио через kie.ai из Chat Actions Open WebUI.
Ноль зависимостей кроме httpx (есть в venv Open WebUI).

Ключевые решения порта:
- Единый клиент с маппингом эндпоинтов (jobs/veo/runway/gpt4o/flux/suno).
- Нормализация 6 разных конвертов ответов в единый {state, urls, fail_msg}.
- Живой каталог моделей: docs.kie.ai/llms.txt (кэш 24ч) + seed-реестр.
- Upload на kieai.redpandaai.co (multipart), polling recordInfo.
- Никаких секретов в логах: ключ только в заголовке Authorization.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Константы (порт src/client.js, src/pricing.js)
# ---------------------------------------------------------------------------

API_BASE = "https://api.kie.ai"
UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
PRICING_URL = "https://api.kie.ai/client/v1/model-pricing/page"
LLMS_TXT_URL = "https://docs.kie.ai/llms.txt"
DOC_BASE = "https://docs.kie.ai/market"

CREATE_ENDPOINTS = {
    "jobs": "/api/v1/jobs/createTask",
    "veo": "/api/v1/veo/generate",
    "runway": "/api/v1/runway/generate",
    "gpt4o": "/api/v1/gpt4o-image/generate",
    "flux": "/api/v1/flux/kontext/generate",
    "suno": "/api/v1/generate",
}
STATUS_ENDPOINTS = {
    "jobs": "/api/v1/jobs/recordInfo",
    "veo": "/api/v1/veo/record-info",
    "runway": "/api/v1/runway/record-detail",
    "gpt4o": "/api/v1/gpt4o-image/record-info",
    "flux": "/api/v1/flux/kontext/record-info",
    "suno": "/api/v1/generate/record-info",
}
CASCADE_ORDER = ["jobs", "veo", "suno", "gpt4o", "flux", "runway"]

CATEGORIES = ["image", "video", "audio"]
APIS = ["jobs", "veo", "runway", "gpt4o", "flux", "suno"]

# Таймауты (порт client.js): POST 120s, GET 60s, upload 300s, download 300s
TIMEOUT_CREATE = 120.0
TIMEOUT_STATUS = 60.0
TIMEOUT_UPLOAD = 300.0
TIMEOUT_DOWNLOAD = 300.0

# Кэш каталога: ~/.velsvisual/models-cache.json (TTL 24ч)
CACHE_DIR = Path(os.path.expanduser("~/.velsvisual"))
MODELS_CACHE_PATH = CACHE_DIR / "models-cache.json"
CACHE_TTL_MS = 24 * 60 * 60 * 1000

USD_PER_CREDIT = 0.005

# ---------------------------------------------------------------------------
# Ошибки (порт client.js KieError / TaskNotFound)
# ---------------------------------------------------------------------------


class KieError(Exception):
    def __init__(self, message: str, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class TaskNotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Seed-реестр (порт src/models.js — 40 моделей)
# ---------------------------------------------------------------------------

DEDICATED_DOC_URLS = {
    "veo": "https://docs.kie.ai/veo3-api/generate-veo-3-video.md",
    "suno": "https://docs.kie.ai/suno-api/generate-music.md",
    "flux": "https://docs.kie.ai/flux-kontext-api/generate-or-edit-image.md",
    "gpt4o": "https://docs.kie.ai/4o-image-api/generate-4-o-image.md",
    "runway": "https://docs.kie.ai/runway-api/generate-ai-video.md",
}


def _m(category, api, prompt_field: str | None = "prompt", image_field=None, image_list=False,
       required=None, defaults=None, description=""):
    entry = {
        "category": category,
        "api": api,
        "prompt_field": prompt_field,
        "image_field": image_field,
        "image_list": bool(image_list),
        "required": required or [],
        "defaults": defaults or {},
        "description": description,
    }
    if api != "jobs":
        entry["dedicated"] = True
        entry["docUrl"] = DEDICATED_DOC_URLS.get(api)
    return entry


SEED_MODELS = {
    # ---- image ----
    "google/nano-banana": _m("image", "jobs", description="Nano Banana — быстрая и дешёвая text-to-image."),
    "google/nano-banana-edit": _m("image", "jobs", image_field="image_urls", image_list=True,
                                  description="Nano Banana edit — редактирование изображения по промпту."),
    "google/imagen4": _m("image", "jobs", description="Imagen 4 — text-to-image."),
    "google/imagen4-fast": _m("image", "jobs", description="Imagen 4 Fast — быстрая text-to-image."),
    "google/imagen4-ultra": _m("image", "jobs", description="Imagen 4 Ultra — максимальное качество."),
    "bytedance/seedream-v4-text-to-image": _m("image", "jobs",
        description="Seedream V4 text-to-image. Опции: image_size, image_resolution (1K|2K|4K), max_images."),
    "bytedance/seedream-v4-edit": _m("image", "jobs", image_field="image_urls", image_list=True,
        description="Seedream V4 edit — редактирование по референсам."),
    "gpt-image/1.5-text-to-image": _m("image", "jobs",
        required=["prompt", "aspect_ratio", "quality"], defaults={"aspect_ratio": "1:1", "quality": "medium"},
        description="GPT Image 1.5 text-to-image. aspect_ratio (1:1|2:3|3:2), quality (medium|high)."),
    "gpt-image/1.5-image-to-image": _m("image", "jobs", image_field="input_urls", image_list=True,
        required=["prompt", "aspect_ratio", "quality", "input_urls"],
        defaults={"aspect_ratio": "3:2", "quality": "medium"},
        description="GPT Image 1.5 image-to-image. Референсы — input_urls."),
    "qwen/text-to-image": _m("image", "jobs", description="Qwen text-to-image — хорош для текста на картинке."),
    "qwen/image-edit": _m("image", "jobs", image_field="image_url",
        description="Qwen image edit — редактирование изображения по промпту."),
    "flux-2/pro-text-to-image": _m("image", "jobs",
        required=["prompt", "aspect_ratio", "resolution"], defaults={"aspect_ratio": "1:1", "resolution": "1K"},
        description="FLUX.2 Pro text-to-image. aspect_ratio (1:1|4:3|3:4|16:9|9:16|3:2), resolution (1K|2K)."),
    "flux-2/flex-image-to-image": _m("image", "jobs", image_field="input_urls", image_list=True,
        required=["prompt", "input_urls", "aspect_ratio", "resolution"],
        defaults={"aspect_ratio": "1:1", "resolution": "1K"},
        description="FLUX.2 Flex image-to-image — генерация по референсам (input_urls)."),
    "grok-imagine/text-to-image": _m("image", "jobs", description="Grok Imagine text-to-image."),
    "z-image": _m("image", "jobs", required=["prompt", "aspect_ratio"], defaults={"aspect_ratio": "1:1"},
        description="Z-Image — лёгкая и быстрая text-to-image. aspect_ratio: 1:1|4:3|3:4|16:9|9:16."),
    "topaz/image-upscale": _m("image", "jobs", prompt_field=None, image_field="image_url",
        required=["image_url"], defaults={"upscale_factor": 2},
        description="Topaz upscale. upscale_factor: 1|2|4 (дефолт 2). Промпт не нужен."),
    "recraft/remove-background": _m("image", "jobs", prompt_field=None, image_field="image",
        required=["image"], description="Recraft — удаление фона. Промпт не нужен."),
    "flux-kontext-pro": _m("image", "flux", image_field="inputImage",
        description="FLUX Kontext Pro — генерация/редактирование. Опции: aspectRatio, outputFormat (jpeg|png), enableTranslation."),
    "flux-kontext-max": _m("image", "flux", image_field="inputImage",
        description="FLUX Kontext Max — топовая версия Kontext."),
    "gpt4o-image": _m("image", "gpt4o", image_field="filesUrl", image_list=True,
        required=["size"], defaults={"size": "1:1"},
        description="GPT-4o Image. size: 1:1|3:2|2:3. filesUrl — до 5 URL. Промпт опционален."),
    # ---- video ----
    "veo3": _m("video", "veo", image_field="imageUrls", image_list=True,
        description="Google Veo 3 — видео со звуком. aspect_ratio (16:9|9:16|Auto), resolution (720p|1080p|4k), generationType, enableTranslation."),
    "veo3_fast": _m("video", "veo", image_field="imageUrls", image_list=True,
        description="Veo 3 Fast — быстрый и дешёвый."),
    "veo3_lite": _m("video", "veo", image_field="imageUrls", image_list=True,
        description="Veo 3 Lite — облегчённый."),
    "runway-gen3": _m("video", "runway", image_field="imageUrl",
        required=["prompt", "duration", "quality"], defaults={"duration": 5, "quality": "720p"},
        description="Runway Gen-3. duration: 5|10, quality: 720p|1080p, aspectRatio: 16:9|4:3|1:1|3:4|9:16."),
    "kling-2.6/text-to-video": _m("video", "jobs",
        description="Kling 2.6 text-to-video. Опции: sound (bool), aspect_ratio, duration (\"5\"|\"10\")."),
    "kling-2.6/image-to-video": _m("video", "jobs", image_field="image_urls", image_list=True,
        description="Kling 2.6 image-to-video. image_urls — максимум 1."),
    "kling/v2-5-turbo-text-to-video-pro": _m("video", "jobs",
        description="Kling 2.5 Turbo text-to-video pro. Опции: duration, aspect_ratio, cfg_scale."),
    "kling/v2-5-turbo-image-to-video-pro": _m("video", "jobs", image_field="image_url",
        description="Kling 2.5 Turbo image-to-video pro. Опции: tail_image_url, duration, cfg_scale."),
    "hailuo/2-3-image-to-video-pro": _m("video", "jobs", image_field="image_url",
        description="Hailuo 2.3 image-to-video pro. duration (\"6\"|\"10\"), resolution (768P|1080P)."),
    "hailuo/02-text-to-video-pro": _m("video", "jobs", description="Hailuo 02 text-to-video pro."),
    "hailuo/02-image-to-video-pro": _m("video", "jobs", image_field="image_url",
        description="Hailuo 02 image-to-video pro."),
    "bytedance/v1-pro-text-to-video": _m("video", "jobs",
        description="Seedance v1 Pro text-to-video. Опции: generate_audio, resolution, duration."),
    "wan/2-6-image-to-video": _m("video", "jobs", image_field="image_urls", image_list=True,
        description="Wan 2.6 image-to-video. duration: 5|10|15, resolution: 720p|1080p, multi_shots."),
    "grok-imagine/text-to-video": _m("video", "jobs", description="Grok Imagine text-to-video."),
    # ---- audio ----
    "suno": _m("audio", "suno",
        description="Suno — музыка. model: V3_5|V4|V4_5|V4_5PLUS|V4_5ALL|V5|V5_5 (дефолт V5). customMode требует style+title. Опции: instrumental, negativeTags, vocalGender (m|f), styleWeight, weirdnessConstraint, audioWeight, duration (только V5_5)."),
    "elevenlabs/text-to-speech-turbo-2-5": _m("audio", "jobs", prompt_field="text",
        description="ElevenLabs TTS Turbo 2.5. text ≤ 5000 симв. Опции: voice, stability, similarity_boost, style, speed (0.7–1.2)."),
    "elevenlabs/text-to-speech-multilingual-v2": _m("audio", "jobs", prompt_field="text",
        required=["text", "voice"], defaults={"voice": "EkK5I93UQWFDigLMpZcX"},
        description="ElevenLabs TTS Multilingual v2. voice обязателен (дефолт EkK5I93UQWFDigLMpZcX)."),
}

POPULAR_FAMILIES = {
    "image": ["nano-banana", "gpt-image", "flux", "seedream", "imagen"],
    "video": ["seedance", "veo", "kling", "hailuo", "sora", "grok-imagine"],
    "audio": ["suno", "elevenlabs"],
}

# ---------------------------------------------------------------------------
# Клиент (порт src/client.js)
# ---------------------------------------------------------------------------


class KieClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}"}

    # -- create ------------------------------------------------------------
    def create(self, api: str, model_id: str, input_data: dict, callback_url: str | None = None) -> str:
        """Создаёт задачу, возвращает taskId."""
        if api not in CREATE_ENDPOINTS:
            raise KieError(f"Неизвестный API: {api}")
        url = API_BASE + CREATE_ENDPOINTS[api]
        if api == "jobs":
            body = {"model": model_id, "input": input_data}
        elif api in ("veo", "flux"):
            body = {**input_data, "model": model_id}
        else:  # runway, gpt4o, suno — model зашит в эндпоинт
            body = {**input_data}
        if callback_url:
            body["callBackUrl"] = callback_url
        resp = httpx.post(url, json=body, headers=self._headers, timeout=TIMEOUT_CREATE)
        data = self._parse(resp)
        task_id = data.get("taskId") or data.get("task_id") or data.get("id")
        if not task_id:
            raise KieError("Ответ не содержит taskId", data=data)
        return str(task_id)

    # -- status ------------------------------------------------------------
    def status(self, api: str, task_id: str) -> dict:
        url = API_BASE + STATUS_ENDPOINTS[api]
        resp = httpx.get(url, params={"taskId": task_id}, headers=self._headers, timeout=TIMEOUT_STATUS)
        data = self._parse(resp)
        return normalize_status(api, data)

    # -- upload ------------------------------------------------------------
    def upload(self, file_path: str | Path, upload_path: str | None = None) -> str:
        """Загружает файл, возвращает downloadUrl."""
        path = Path(file_path)
        if not path.is_file():
            raise KieError(f"Файл не найден: {path}")
        up = upload_path or guess_upload_path(path.name)
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            data = {"uploadPath": up, "fileName": path.name}
            resp = httpx.post(UPLOAD_URL, files=files, data=data, headers=self._headers, timeout=TIMEOUT_UPLOAD)
        data = self._parse(resp)
        url = extract_file_url(data)
        if not url:
            raise KieError("Upload не вернул URL", data=data)
        return url

    # -- credits -----------------------------------------------------------
    def credits(self) -> dict:
        resp = httpx.get(API_BASE + "/api/v1/chat/credit", headers=self._headers, timeout=TIMEOUT_STATUS)
        data = self._parse(resp)
        return data

    # -- download ----------------------------------------------------------
    def download(self, url: str, dest: Path) -> Path:
        resp = httpx.get(url, timeout=TIMEOUT_DOWNLOAD, follow_redirects=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest

    # -- helpers -----------------------------------------------------------
    def _parse(self, resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except Exception:
            raise KieError(f"HTTP {resp.status_code}: не-JSON ответ")
        if resp.status_code == 401:
            raise KieError("Неверный API-ключ (401)", code=401)
        if resp.status_code == 402:
            raise KieError("Недостаточно кредитов (402)", code=402)
        if resp.status_code == 422:
            raise KieError(f"Ошибка валидации (422): {data}", code=422)
        if resp.status_code == 429:
            raise KieError("Rate limit (429)", code=429)
        if resp.status_code == 451:
            raise KieError("Входное изображение не скачалось (451)", code=451)
        if resp.status_code == 455:
            raise KieError("Сервис на обслуживании (455)", code=455)
        if resp.status_code == 501:
            raise KieError("Генерация не удалась (501)", code=501)
        if resp.status_code >= 400:
            raise KieError(f"HTTP {resp.status_code}: {data}", code=resp.status_code)
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise KieError(f"KIE: {data.get('msg') or data.get('message') or data}", code=data.get("code"))
        return data if isinstance(data, dict) else {"data": data}


def guess_upload_path(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in ("mp3", "wav", "flac", "m4a", "ogg", "aac"):
        return "audio"
    if ext in ("mp4", "mov", "avi", "mkv", "webm"):
        return "videos"
    return "images"


def extract_file_url(data: Any) -> str | None:
    """Извлечение URL из меняющихся схем ответа upload API (порт client.js)."""
    if not isinstance(data, dict):
        return None
    for key in ("downloadUrl", "fileUrl", "url", "fileURL", "download_url", "file_url"):
        v = data.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for key in ("data", "result", "file"):
        v = data.get(key)
        if isinstance(v, dict):
            found = extract_file_url(v)
            if found:
                return found
    return None


def normalize_status(api: str, data: dict) -> dict:
    """Унификация 6 конвертов ответов в {state, urls, fail_msg} (порт client.js)."""
    if api == "jobs":
        state = data.get("state")
        if state == "success":
            urls = []
            rj = data.get("resultJson")
            if isinstance(rj, str):
                try:
                    parsed = json.loads(rj)
                    urls = parsed.get("resultUrls", []) if isinstance(parsed, dict) else []
                except Exception:
                    urls = []
            return {"state": "success", "urls": urls, "fail_msg": None}
        if state == "fail":
            return {"state": "fail", "urls": [], "fail_msg": data.get("failMsg") or data.get("fail_msg")}
        return {"state": "pending", "urls": [], "fail_msg": None}

    if api == "veo":
        flag = data.get("successFlag")
        if flag == 1:
            return {"state": "success", "urls": (data.get("response") or {}).get("resultUrls", []), "fail_msg": None}
        if flag in (2, 3):
            return {"state": "fail", "urls": [], "fail_msg": data.get("errorMessage") or data.get("errorMsg")}
        return {"state": "pending", "urls": [], "fail_msg": None}

    if api == "flux":
        flag = data.get("successFlag")
        if flag == 1:
            return {"state": "success", "urls": [data.get("resultImageUrl")] if data.get("resultImageUrl") else [], "fail_msg": None}
        if flag in (2, 3):
            return {"state": "fail", "urls": [], "fail_msg": data.get("errorMessage")}
        return {"state": "pending", "urls": [], "fail_msg": None}

    if api == "runway":
        state = data.get("state")
        if state == "success":
            urls = []
            vi = data.get("videoInfo") or {}
            if vi.get("videoUrl"):
                urls = [vi["videoUrl"]]
            else:
                urls = (data.get("response") or {}).get("resultUrls", [])
            return {"state": "success", "urls": urls, "fail_msg": None}
        if state == "fail":
            return {"state": "fail", "urls": [], "fail_msg": data.get("failMsg")}
        return {"state": "pending", "urls": [], "fail_msg": None}

    if api == "suno":
        status = data.get("status")
        if status == "SUCCESS":
            tracks = (data.get("response") or {}).get("sunoData", [])
            urls = []
            for t in tracks:
                if t.get("audioUrl"):
                    urls.append(t["audioUrl"])
                elif t.get("streamAudioUrl"):
                    urls.append(t["streamAudioUrl"])
            return {"state": "success", "urls": urls, "fail_msg": None}
        if status and status.startswith("FAILED") or status == "SENSITIVE_WORD_ERROR":
            return {"state": "fail", "urls": [], "fail_msg": status}
        return {"state": "pending", "urls": [], "fail_msg": None}

    if api == "gpt4o":
        flag = data.get("successFlag")
        if flag == 1:
            return {"state": "success", "urls": (data.get("response") or {}).get("resultUrls", []), "fail_msg": None}
        if flag in (2, 3):
            return {"state": "fail", "urls": [], "fail_msg": data.get("errorMessage")}
        # fallback на jobs-форму (порт client.js)
        if flag is None and "state" in data:
            return normalize_status("jobs", data)
        return {"state": "pending", "urls": [], "fail_msg": None}

    return {"state": "pending", "urls": [], "fail_msg": None}


def poll_until_done(client: KieClient, api: str, task_id: str, interval: float = 5.0, timeout: float = 600.0):
    """Polling до success/fail (порт cli.js pollUntilDone)."""
    deadline = time.monotonic() + timeout
    while True:
        st = client.status(api, task_id)
        if st["state"] == "success":
            return st
        if st["state"] == "fail":
            raise KieError(f"Задача {task_id} завершилась ошибкой: {st['fail_msg']}")
        if time.monotonic() > deadline:
            raise KieError(f"Таймаут {int(timeout)}с. Задача {task_id} ещё выполняется.")
        time.sleep(interval)


def detect_api(client: KieClient, task_id: str) -> str:
    """Каскад автоопределения API по taskId (порт cli.js detectApi)."""
    for api in CASCADE_ORDER:
        try:
            client.status(api, task_id)
            return api
        except TaskNotFound:
            continue
        except KieError:
            continue
    raise TaskNotFound(f"Задача {task_id} не найдена ни в одном API")


# ---------------------------------------------------------------------------
# Живой каталог (порт src/registry.js — упрощённый: llms.txt без схем)
# ---------------------------------------------------------------------------


def _parse_llms_txt(text: str) -> list[dict]:
    """Парсинг docs.kie.ai/llms.txt → [{id, category, url, title}].

    Точный порт parseLlmsTxt из src/registry.js:
    строка вида "- Image Models > Google [Google - Nano Banana](https://docs.kie.ai/market/google/nano-banana.md): описание"
    Категории: image/video/music Models; Chat Models и /cn/ пропускаются.
    """
    entries = []
    seen = set()
    line_re = re.compile(
        r"^-\s+([A-Za-z][A-Za-z ]*?Models)\b[^[]*\[([^\]]+)\]\((https://docs\.kie\.ai/market/[^)\s]+?\.md)\)\s*:?\s*(.*)$"
    )
    cat_by_breadcrumb = {
        "image models": "image",
        "video models": "video",
        "music models": "audio",
    }
    for raw in text.splitlines():
        m = line_re.match(raw.strip())
        if not m:
            continue
        breadcrumb, title, url, description = m.groups()
        if "/cn/" in url:
            continue
        category = cat_by_breadcrumb.get(breadcrumb.replace(r"\s+", " ").lower())
        if not category or url in seen:
            continue
        seen.add(url)
        # id модели = путь market-страницы без .md (например "google/nano-banana")
        model_id = re.sub(r"\.md$", "", url.split("/market/")[1])
        desc = description.strip()
        entries.append({
            "id": model_id,
            "category": category,
            "url": url,
            "title": title,
            "description": "" if desc.startswith("#") else desc,
        })
    return entries


def load_registry(api_key: str, refresh: bool = False) -> dict:
    """Реестр моделей: live llms.txt → кэш → seed. Возвращает {models: {id: entry}, source}."""
    cache = None
    if MODELS_CACHE_PATH.exists():
        try:
            cache = json.loads(MODELS_CACHE_PATH.read_text())
        except Exception:
            cache = None
    fresh = cache and (time.time() * 1000 - cache.get("fetchedAtMs", 0)) < CACHE_TTL_MS

    live_entries = []
    if (refresh or not fresh) and api_key:
        try:
            resp = httpx.get(LLMS_TXT_URL, timeout=30)
            if resp.status_code == 200:
                live_entries = _parse_llms_txt(resp.text)
                if live_entries:
                    MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    MODELS_CACHE_PATH.write_text(json.dumps(
                        {"fetchedAtMs": int(time.time() * 1000), "entries": live_entries}))
        except Exception:
            pass

    if not live_entries and cache:
        live_entries = cache.get("entries", [])

    models = {}
    for e in live_entries:
        models[e["id"]] = {
            "category": e["category"],
            "api": "jobs",
            "prompt_field": "prompt",
            "image_field": None,
            "image_list": False,
            "required": [],
            "defaults": {},
            "description": f"Модель из живого каталога kie.ai ({e['category']}).",
            "dynamic": True,
        }
    # seed приоритетен: перезаписывает динамику
    for mid, entry in SEED_MODELS.items():
        models[mid] = dict(entry)
    return {"models": models, "source": "live" if live_entries else ("cache" if cache else "seed")}


# ---------------------------------------------------------------------------
# Recommend (порт src/recommend.js)
# ---------------------------------------------------------------------------


def family_of(model_id: str) -> dict:
    norm = re.sub(r"[/_\s]+", "-", str(model_id).lower())
    norm = re.sub(r"v(?=\d)", "", norm)
    m = re.search(r"(\d+)(?:[.-](\d+))?", norm)
    if not m:
        return {"family": norm, "version": 0, "suffix": ""}
    family = norm[: m.start()].rstrip("-")
    minor = float(m.group(2)) / (10 ** len(m.group(2))) if m.group(2) else 0
    suffix = norm[m.end():].lstrip("-")
    return {"family": family, "version": int(m.group(1)) + minor, "suffix": suffix}


def _popularity_index(category: str, family: str) -> int:
    keys = POPULAR_FAMILIES.get(category, [])
    for i, key in enumerate(keys):
        if key in family:
            return i
    return 10**9


def recommend(category: str, registry: dict, limit: int = 4) -> list[dict]:
    """Топовая модель каждого популярного семейства (порт recommend.js, без цен)."""
    families: dict[str, list[tuple[str, dict]]] = {}
    for mid, entry in registry["models"].items():
        if entry.get("category") != category or entry.get("stale"):
            continue
        fam = family_of(mid)["family"]
        families.setdefault(fam, []).append((mid, entry))

    tops = []
    for fam, candidates in families.items():
        # сортировка: версия → суффикс (базовый короче) → id
        candidates.sort(key=lambda c: (-family_of(c[0])["version"], len(family_of(c[0])["suffix"]), c[0]))
        tops.append({"family": fam, "pop": _popularity_index(category, fam), "top": candidates[0]})

    tops.sort(key=lambda t: (t["pop"], -family_of(t["top"][0])["version"], t["family"]))
    seen = set()
    chosen = []
    for t in tops:
        key = None
        for k in POPULAR_FAMILIES.get(category, []):
            if k in t["family"]:
                key = k
                break
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        chosen.append({"family": t["family"], "model": t["top"][0], "description": t["top"][1].get("description", "")})
        if len(chosen) >= limit:
            break
    return chosen


# ---------------------------------------------------------------------------
# Поиск моделей (порт cli.js matchesSearch/expandSearchTerms — упрощённый)
# ---------------------------------------------------------------------------

SEARCH_SYNONYMS = {
    "edit": ["image-to-image", "i2i", "img2img", "remix", "inpaint", "edit"],
    "upscale": ["upscale", "enhance", "super-resolution"],
    "remove-background": ["background", "bg", "transparent", "remove-background"],
    "image-to-video": ["i2v", "image-to-video", "img2video"],
    "text-to-video": ["t2v", "text-to-video"],
    "text-to-image": ["t2i", "text-to-image", "image generation"],
    "music": ["song", "audio", "music", "suno"],
    "tts": ["speech", "voice", "tts", "text-to-speech"],
}


def search_models(registry: dict, query: str, category: str | None = None, limit: int = 10) -> list[dict]:
    q = query.lower().strip()
    terms = set(SEARCH_SYNONYMS.get(q, [q]))
    results = []
    for mid, entry in registry["models"].items():
        if category and entry.get("category") != category:
            continue
        hay = f"{mid} {entry.get('description', '')}".lower()
        if any(t in hay for t in terms):
            results.append({"id": mid, "category": entry.get("category"), "description": entry.get("description", "")})
    results.sort(key=lambda r: r["id"])
    return results[:limit]
