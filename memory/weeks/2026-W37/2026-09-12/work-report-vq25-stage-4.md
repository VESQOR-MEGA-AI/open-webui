# Work Report: VQ-25 — стадия 4 (все судьи, тэлли в коде, флаги self-vote)
*Date: 2026-09-12*
*Agents: architect, vibe-mentor (чекпоинт ×1), code (реализация ×4), review (три оси ×2)*
*Status: ✅ Complete*

## Problem

Стадия 4 по PLAN-RU.md §5: «Run all three judges», подсчёт **в коде** из валидированных вердиктов, флаги self-vote. Решение владельца перед стартом (`DECISIONS.md#012`): **устаревшие вердикты не считаются** — только свежие по текущим версиям ответов.

## What Was Done

1. **`utils/answer_compare_tally.py`** — чистый модуль: включается вердикт, чей текущий (последний `complete`) отчёт свежий (`judged_versions` = текущие версии как множества); остальные исключаются с детерминированной причиной (`outdated` > `failed`/`malformed`; провал пересуда — доп. поле `latest_attempt`; `not_configured` только без отчёта; `unmappable` — сверх спеки, чтобы одна испорченная строка не роняла весь `GET`). `tie`/`no_reliable_winner` — в знаменатель, не в числитель; «предпочтённый» — строгое большинство `votes*2 > n_included`. Флаги self-vote (в т.ч. внутри ничьей) аннотируют, не меняют; исключённые самоголосования видны отдельно. `partial` — относительно трёх систем. `included_reports` фиксирует ревизии отчётов для стадии 5. Ни одного поля, читающегося как уверенность/консенсус (тест с рекурсивным обходом ключей).
2. **Run-all** `POST /runs/{id}/reports` — через общую `judge_once` (одиночный путь и run-all — одна реализация слепоты, вставки, лестницы); три судьи параллельно, три разных `label_map`; per-judge предусловия — записи `skipped`; неожиданное исключение — `failed: internal` с трассировкой, остальные стоят, 200. `tally` в run-all и в `GET /runs/{id}`, на чтении, без персистенции.
3. **Страница**: кнопка «Run all three judges», `tallyState.ts` (единственная точка записи, ноль пересчёта — панель получает один объект и не имеет доступа к отчётам), `TallyPanel.svelte` с заголовком «{votes} of {n_included}», голосами, partial с исключёнными в простых словах, ничьими и inconclusive прямым текстом, self-vote аннотацией и фиксированным футером «Agreement between judges is not evidence of correctness.». Тэлли перерисовывается из каждого `GET`; `clearTally()` при новом запуске.

## Changed Files

| File | Action | Description |
|---|---|---|
| `backend/open_webui/utils/answer_compare_tally.py` | создан | Тэлли, чистые функции |
| `backend/open_webui/routers/answer_compare.py` | изменён | `judge_once`, `POST /runs/{id}/reports`, `tally` в `GET` |
| `test/test_vq25_tally.py` | создан | 33 теста |
| `src/lib/components/admin/AnswerCompare/tallyState.ts`, `tallyState.test.ts`, `TallyPanel.svelte` | созданы | Состояние и панель |
| `src/lib/components/admin/AnswerCompare.svelte`, `src/lib/apis/answer-compare/index.ts` | изменены | Run-all, `applyTally`, типы |
| `test/test_vq25_page_wiring.py`, `translation.json` | изменены | Структурные проверки, 19 ключей |

## Verification

Архитектором: `pytest test/ -q` → **171 passed** · `CI=true npx vitest --run` → **53 passed** · три standalone-гейта ALL CHECKS PASSED · `svelte-check` 8408 — базовая линия · `vite build` успешна при 681 МБ · `webui.db` не тронута. Ревью 4a: PASS, **20/20** мутантов. Ревью 4b: PASS, 11/14, три выживших закрыты и перепроверены.

## Lessons Learned

- **Граница правила — отдельный тест.** Мутант «нестрогое большинство» выжил, потому что был тест на «2 из 3», но не на «1 из 2». Каждое неравенство в правиле заслуживает теста ровно на границу.
- **Тест «панель не считает сама» строится на противоречии.** Единственный способ поймать пересчёт — подсунуть данные, где серверный результат расходится с тем, что можно пересчитать, и требовать серверный.
- **Одна испорченная строка не должна ронять чтение.** Тэлли, вызывающий строгий маппинг, превратил бы `GET` в 500 из-за одного битого отчёта; исключение с причиной честнее.
- **Свежесть монотонна** — версии ответов только растут, отката нет, поэтому «устаревший» — конечное состояние; это упрощает и тэлли, и будущую сводку.
