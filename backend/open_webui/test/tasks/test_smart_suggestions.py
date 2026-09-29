"""Unit tests for the smart suggestions prompt builder and parser.

They import the two pure helpers from the tasks router without spinning up
FastAPI — the request path is exercised by the running application.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]  # /root/owui-suggestions/backend


def _load_router_module():
    """Import routers/tasks.py without the whole app package wiring."""
    root = str(REPO)
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(
        'open_webui.routers.tasks', REPO / 'open_webui' / 'routers' / 'tasks.py'
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tasks = _load_router_module()


@pytest.fixture(autouse=True)
def clear_cache():
    tasks._suggestions_cache.clear()
    yield
    tasks._suggestions_cache.clear()


def test_prompt_includes_titles_and_memories():
    prompt = tasks.build_smart_suggestions_prompt(
        ['Q3 pricing plan', 'Vendor comparison'], ['Prefers short answers']
    )
    assert 'Q3 pricing plan' in prompt
    assert 'Vendor comparison' in prompt
    assert 'Prefers short answers' in prompt


def test_prompt_names_no_context_when_history_is_empty():
    prompt = tasks.build_smart_suggestions_prompt([], [])
    assert 'No prior context' in prompt


def test_parser_accepts_valid_json_with_padding():
    raw = 'Here you go:\n{"suggestions": [{"title": ["Next step", "for the pricing plan"], "content": "Draft the Q3 pricing plan"}]}\n'
    parsed = tasks.parse_smart_suggestions(raw)
    assert parsed == [{'title': ['Next step', 'for the pricing plan'], 'content': 'Draft the Q3 pricing plan'}]


def test_parser_rejects_malformed_and_wrong_shapes():
    assert tasks.parse_smart_suggestions('no json at all') == []
    assert tasks.parse_smart_suggestions('{"suggestions": [3]}') == []
    assert tasks.parse_smart_suggestions('{"suggestions": [{"title": ["only", "two"], "content": ""}]}') == []


def test_parser_caps_at_four_items():
    items = ', '.join(f'{{"title": ["t{i}", "x"], "content": "c{i}"}}' for i in range(6))
    parsed = tasks.parse_smart_suggestions('{"suggestions": [%s]}' % items)
    assert len(parsed) == tasks.SUGGESTIONS_COUNT
