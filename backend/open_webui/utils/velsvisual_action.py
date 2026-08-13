"""
title: VelsVisual — генерация через kie.ai
author: VESQOR
version: 0.1.0
description: Генерация изображений, видео и музыки через KIE API (kie.ai) прямо из чата. Кнопки на сообщении: Generate (по промпту), Recommend (подбор модели), Models (каталог), Pricing (баланс). Порт клиента VelsVisual (github.com/nick-vels/VelsVisual, MIT).
license: MIT
"""

import json
import os
import re
import time
from typing import Any, Optional

import httpx

from open_webui.utils import velsvisual_kie as vk
from pydantic import BaseModel, Field


class Valves(BaseModel):
    KIE_API_KEY: str = Field(
        default="",
        description="API-ключ kie.ai (https://kie.ai). Без него генерация недоступна.",
    )
    ENHANCE_API_BASE: str = Field(
        default="https://ollama.com/v1",
        description="Base URL OpenAI-совместимого API для улучшения промптов (наш стек: ollama.com/v1). Пусто — улучшение отключено.",
    )
    ENHANCE_API_KEY: str = Field(
        default="",
        description="API-ключ для улучшения промптов (ollama.com). Пусто — улучшение отключено.",
    )
    ENHANCE_MODEL: str = Field(
        default="glm-5.2",
        description="Модель для улучшения промптов (reasoning-модель, требует max_tokens >= 4000).",
    )
    POLL_INTERVAL: float = Field(
        default=5.0,
        description="Интервал опроса статуса задачи, секунд.",
    )
    POLL_TIMEOUT: float = Field(
        default=600.0,
        description="Максимальное время ожидания генерации, секунд.",
    )
    MAX_IMAGES: int = Field(
        default=4,
        description="Максимум изображений для image-to-image/видео-моделей (URL из сообщения).",
    )


class UserValves(BaseModel):
    pass


class Action:
    def __init__(self):
        self.valves = Valves()
        self.actions = [
            {
                "id": "generate",
                "name": "Generate",
                "description": "Сгенерировать изображение/видео/музыку по промпту через kie.ai",
                "icon_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M12 5v14M5 12h14'/></svg>",
            },
            {
                "id": "recommend",
                "name": "Recommend",
                "description": "Подобрать модель под задачу (последняя версия популярного семейства)",
                "icon_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><path d='M12 2l2.4 7.2H22l-6 4.6 2.3 7.2-6.3-4.5-6.3 4.5L8 13.8l-6-4.6h7.6z'/></svg>",
            },
            {
                "id": "models",
                "name": "Models",
                "description": "Показать каталог моделей kie.ai (--category image|video|audio)",
                "icon_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><rect x='3' y='3' width='7' height='7'/><rect x='14' y='3' width='7' height='7'/><rect x='3' y='14' width='7' height='7'/><rect x='14' y='14' width='7' height='7'/></svg>",
            },
            {
                "id": "pricing",
                "name": "Pricing",
                "description": "Показать баланс кредитов kie.ai",
                "icon_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'><circle cx='12' cy='12' r='9'/><path d='M12 7v10M9 9.5c0-1 1.3-1.5 3-1.5s3 .5 3 1.5-1.3 1.5-3 1.5-3 .5-3 1.5 1.3 1.5 3 1.5 3-.5 3-1.5'/></svg>",
            },
        ]

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _client(valves) -> vk.KieClient:
        return vk.KieClient(valves.KIE_API_KEY.strip())

    # ------------------------------------------------------------------ prompt enhancement (наш стек: ollama.com/v1)

    _ENHANCE_SYSTEM = (
        "You are a professional visualization prompt engineer for business intelligence. "
        "The user needs a visual artifact for a business/technical/operational purpose: "
        "a chart, diagram, infographic, dashboard mock, process map, architecture diagram, "
        "or presentation visual. "
        "Rewrite the user's request into a detailed, high-quality image-generation prompt in English. "
        "Include: exact visual type, data/relationships to show, layout, style (clean corporate, "
        "flat design, etc.), color palette, labels, and quality tags (e.g. 'ultra-detailed, 4k, "
        "professional business graphic'). "
        "If the request is NOT business-related (e.g. art, fantasy, personal entertainment), "
        "say exactly: 'NOT_BUSINESS' and nothing else. "
        "Output ONLY the prompt text — no explanations, no quotes, no markdown."
    )

    @staticmethod
    def _enhance_prompt(valves, prompt: str) -> str | None:
        """Улучшение промпта через наш стек (ollama.com/v1, glm-5.2).

        Возвращает улучшенный промпт или None, если улучшение недоступно/не удалось
        или запрос вне бизнес-профиля (NOT_BUSINESS).
        """
        base = (valves.ENHANCE_API_BASE or "").strip().rstrip("/")
        key = (valves.ENHANCE_API_KEY or "").strip()
        if not base or not key:
            return None
        url = f"{base}/chat/completions"
        payload = {
            "model": (valves.ENHANCE_MODEL or "glm-5.2").strip(),
            "messages": [
                {"role": "system", "content": Action._ENHANCE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            # reasoning-модели (glm-5.2) тратят бюджет на reasoning — нужен запас
            "max_tokens": 4000,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            content = (content or "").strip()
            if not content:
                return None
            # снять обёртку markdown-кода, если модель завернула
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
                content = re.sub(r"\n?```$", "", content).strip()
            if "NOT_BUSINESS" in content.upper():
                return None
            return content
        except Exception:
            return None

    @staticmethod
    def _last_user_content(body: dict) -> str:
        """Последний user-контент из истории сообщений (порт cli.js: последний user message)."""
        messages = body.get("messages") or []
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content") or ""
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict):
                            parts.append(p.get("text") or "")
                        else:
                            parts.append(str(p))
                    content = " ".join(parts)
                return str(content)
        return ""

    @staticmethod
    def _extract_image_urls(content: str, max_images: int) -> list[str]:
        """URL изображений из markdown-контента сообщения."""
        urls = re.findall(r"https?://[^\s)\]]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s)\]]*)?", content, re.I)
        return urls[:max_images]

    @staticmethod
    def _parse_prompt(content: str) -> dict:
        """Разбор промпта: --model, --set k=v, --category, --count. Порт cli.js parseArgs (упрощённый)."""
        model = None
        category = None
        sets = {}
        tokens = content.split()
        rest = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == "--model" and i + 1 < len(tokens):
                model = tokens[i + 1]
                i += 2
                continue
            if t == "--category" and i + 1 < len(tokens):
                category = tokens[i + 1]
                i += 2
                continue
            if t == "--set" and i + 1 < len(tokens):
                kv = tokens[i + 1]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    sets[k] = Action._parse_set_value(v)
                i += 2
                continue
            rest.append(t)
            i += 1
        return {
            "model": model,
            "category": category,
            "sets": sets,
            "prompt": " ".join(rest).strip(),
        }

    @staticmethod
    def _parse_set_value(v: str):
        """JSON-парсинг значений --set (порт cli.js parseSetValue)."""
        if v.lower() == "true":
            return True
        if v.lower() == "false":
            return False
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
        if v.startswith("[") and v.endswith("]"):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    @staticmethod
    def _detect_category(prompt: str) -> str:
        """Определение категории по ключевым словам промпта (эвристика, не классификатор)."""
        p = prompt.lower()
        if any(w in p for w in ("видео", "video", "клип", "анимац", "ролик", "мульт")):
            return "video"
        if any(w in p for w in ("музык", "песн", "трек", "music", "song", "audio", "озвуч", "голос", "мелоди")):
            return "audio"
        return "image"

    @staticmethod
    def _build_input(entry: dict, prompt: str, sets: dict, image_urls: list[str]) -> dict:
        """Сборка input для модели (порт cli.js buildInput)."""
        inp = {}
        pf = entry.get("prompt_field")
        if pf and prompt:
            inp[pf] = prompt
        if entry.get("image_field") and image_urls:
            if entry.get("image_list"):
                inp[entry["image_field"]] = image_urls
            else:
                inp[entry["image_field"]] = image_urls[0]
        # дефолты обязательных полей
        for k, v in (entry.get("defaults") or {}).items():
            if k not in inp:
                inp[k] = v
        # --set переопределяет всё
        inp.update(sets)
        return inp

    @staticmethod
    def _validate_input(entry: dict, inp: dict) -> str | None:
        """Проверка обязательных полей (порт cli.js validateInput). Возвращает ошибку или None."""
        for field in entry.get("required") or []:
            if field not in inp or inp[field] in (None, "", []):
                return f"Модель требует поле '{field}'. Добавь --set {field}=значение"
        return None

    @staticmethod
    def _format_result(st: dict, model_id: str, elapsed: float) -> str:
        urls = st.get("urls") or []
        if not urls:
            return f"**{model_id}** — задача завершена, но результат пуст."
        lines = [f"**{model_id}** — готово за {elapsed:.0f}с:"]
        for u in urls:
            if re.search(r"\.(mp4|webm|mov)(\?|$)", u, re.I):
                lines.append(f"[▶ Скачать видео]({u})")
            elif re.search(r"\.(mp3|wav|flac|m4a|ogg)(\?|$)", u, re.I):
                lines.append(f"[🎵 Скачать аудио]({u})")
            else:
                lines.append(f"![результат]({u})")
        return "\n".join(lines)

    @staticmethod
    def _model_choice(registry: dict, model: str | None, category: str | None, prompt: str) -> tuple[str, dict]:
        """Выбор модели: явный id → поиск по подстроке → recommend. Возвращает (id, entry)."""
        if model:
            if model in registry["models"]:
                return model, registry["models"][model]
            for mid in registry["models"]:
                if model.lower() in mid.lower():
                    return mid, registry["models"][mid]
            raise vk.KieError(f"Модель '{model}' не найдена в реестре. Смотри кнопку Models.")
        cat = category or Action._detect_category(prompt)
        recs = vk.recommend(cat, registry, limit=4)
        if not recs:
            raise vk.KieError(f"Нет моделей в категории '{cat}'.")
        return recs[0]["model"], registry["models"][recs[0]["model"]]

    @staticmethod
    def _emit(__event_emitter__, description: str, done: bool = False):
        if __event_emitter__ is None:
            return
        try:
            __event_emitter__({
                "type": "status",
                "data": {"description": description, "done": done},
            })
        except Exception:
            pass

    @staticmethod
    def _run_generation(valves, entry: dict, prompt: str, sets: dict, image_urls: list[str], __event_emitter__) -> tuple[dict, float]:
        client = Action._client(valves)
        inp = Action._build_input(entry, prompt, sets, image_urls)
        err = Action._validate_input(entry, inp)
        if err:
            raise vk.KieError(err)
        Action._emit(__event_emitter__, f"Создаю задачу: {entry.get('api', 'jobs')} / {entry.get('category', '?')}…")
        task_id = client.create(entry["api"], entry.get("id", ""), inp)
        Action._emit(__event_emitter__, f"Задача {task_id} — жду результат…")
        t0 = time.monotonic()
        st = vk.poll_until_done(client, entry["api"], task_id, valves.POLL_INTERVAL, valves.POLL_TIMEOUT)
        return st, time.monotonic() - t0

    @staticmethod
    def _format_models_list(registry: dict, category: str | None, limit: int = 25) -> str:
        lines = ["**Каталог моделей kie.ai:**", ""]
        cats = [category] if category else ["image", "video", "audio"]
        for cat in cats:
            lines.append(f"### {cat}")
            items = []
            for mid, e in registry["models"].items():
                if e.get("category") != cat:
                    continue
                items.append((mid, e.get("description", "")))
            items.sort(key=lambda x: x[0])
            for mid, desc in items[:limit]:
                lines.append(f"- `{mid}` — {desc[:120]}")
            if len(items) > limit:
                lines.append(f"- … и ещё {len(items) - limit} моделей")
            lines.append("")
        lines.append("_Использование: `--model <id>` в промпте, например: `кот в космосе --model google/nano-banana`_")
        return "\n".join(lines)

    @staticmethod
    def _format_recommend(registry: dict, category: str) -> str:
        recs = vk.recommend(category, registry, limit=4)
        if not recs:
            return f"Нет моделей в категории '{category}'."
        lines = [f"**Рекомендации: {category}**", ""]
        for r in recs:
            lines.append(f"- `{r['model']}` — {r['description'][:150]}")
        lines.append("")
        lines.append("_Скопируй id модели в промпт: `--model <id>`_")
        return "\n".join(lines)

    @staticmethod
    def _format_pricing(valves) -> str:
        client = Action._client(valves)
        try:
            data = client.credits()
        except Exception as exc:
            return f"Не удалось получить баланс: {exc}"
        info = data.get("data") or data
        total = info.get("totalCredits") or info.get("total_credits") or "?"
        used = info.get("usedCredits") or info.get("used_credits") or "?"
        left = info.get("remainingCredits") or info.get("remaining_credits") or "?"
        return f"**Баланс kie.ai:**\n- Всего: {total}\n- Использовано: {used}\n- Осталось: {left}"

    @staticmethod
    def _update_message(body: dict, content: str) -> dict:
        """Возвращает {messages: [...]} для обновления сообщения в чате.

        Фронт (Chat.svelte chatActionHandler) обновляет history.messages по id —
        новые сообщения он НЕ создаёт и не рендерит. Поэтому action обязан
        вернуть сообщение с id СУЩЕСТВУЮЩЕГО (body.id = id ответа модели),
        иначе результат не появится в UI.
        """
        return {"messages": [{"id": body.get("id"), "content": content}]}

    # ------------------------------------------------------------------ entry point

    async def action(self, body: dict, __model__: dict | None = None, __id__: str | None = None,
                     __event_emitter__: Any = None, __user__: dict | None = None) -> dict:
        """Обработчик Chat Action. __id__ = sub_action_id (generate|recommend|models|pricing)."""
        valves = getattr(self, "valves", None)
        if not valves or not valves.KIE_API_KEY.strip():
            return self._update_message(body, "⚠️ KIE_API_KEY не настроен. Попроси администратора задать ключ в настройках функции VelsVisual.")

        # Fallback на env: если valves пустые — берём ключи enhancer из окружения
        if not (valves.ENHANCE_API_BASE or "").strip():
            valves.ENHANCE_API_BASE = os.environ.get("ENHANCE_API_BASE", "") or ""
        if not (valves.ENHANCE_API_KEY or "").strip():
            valves.ENHANCE_API_KEY = os.environ.get("ENHANCE_API_KEY", "") or ""
        if not (valves.ENHANCE_MODEL or "").strip():
            valves.ENHANCE_MODEL = os.environ.get("ENHANCE_MODEL", "glm-5.2") or "glm-5.2"

        sub = __id__ or "generate"
        content = self._last_user_content(body)

        try:
            if sub == "models":
                registry = vk.load_registry(valves.KIE_API_KEY.strip())
                cat = None
                m = re.search(r"--category\s+(\w+)", content)
                if m:
                    cat = m.group(1)
                return self._update_message(body, self._format_models_list(registry, cat))

            if sub == "recommend":
                registry = vk.load_registry(valves.KIE_API_KEY.strip())
                cat = self._detect_category(content)
                return self._update_message(body, self._format_recommend(registry, cat))

            if sub == "pricing":
                return self._update_message(body, self._format_pricing(valves))

            # sub == "generate" (по умолчанию)
            parsed = self._parse_prompt(content)
            if not parsed["prompt"] and not parsed["model"]:
                return self._update_message(body, "Опиши, что сгенерировать, например: `кот в космосе --model google/nano-banana`")

            registry = vk.load_registry(valves.KIE_API_KEY.strip())
            model_id, entry = self._model_choice(registry, parsed["model"], parsed["category"], parsed["prompt"])
            image_urls = self._extract_image_urls(content, valves.MAX_IMAGES)

            # Улучшение промпта нашим стеком (если настроено и не --raw)
            final_prompt = parsed["prompt"]
            if not parsed["sets"].pop("raw", False):
                enhanced = self._enhance_prompt(valves, parsed["prompt"])
                if enhanced:
                    final_prompt = enhanced
                    self._emit(__event_emitter__, f"✨ Промпт улучшен: {final_prompt[:200]}")
                else:
                    self._emit(__event_emitter__, "Улучшение промпта недоступно или запрос вне бизнес-профиля — отправляю как есть")

            self._emit(__event_emitter__, f"Модель: {model_id} ({entry.get('category')})")
            st, elapsed = self._run_generation(valves, {**entry, "id": model_id}, final_prompt, parsed["sets"], image_urls, __event_emitter__)
            self._emit(__event_emitter__, "Готово", done=True)
            return self._update_message(body, self._format_result(st, model_id, elapsed))

        except vk.KieError as exc:
            self._emit(__event_emitter__, f"Ошибка: {exc}", done=True)
            return self._update_message(body, f"❌ **Ошибка:** {exc}")
        except Exception as exc:
            self._emit(__event_emitter__, f"Ошибка: {exc}", done=True)
            return self._update_message(body, f"❌ **Ошибка:** {exc}")
