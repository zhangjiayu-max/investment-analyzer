"""视觉配置继承、历史凭据清理和鉴权失败行为。"""
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError

import config
from db import config as config_db
from db._conn import _get_conn
from services.content import image_parser


def test_default_vision_config_has_no_embedded_credentials():
    values = {row[0]: row[1] for row in config_db.DEFAULT_CONFIGS}
    assert values['vision.mimo.api_key'] == ''
    assert values['vision.mimo.base_url'] == ''
    assert values['vision.mimo.model'] == ''


def test_vision_inherits_current_plan_configuration(monkeypatch):
    monkeypatch.delenv('VISION_API_KEY', raising=False)
    monkeypatch.delenv('VISION_BASE_URL', raising=False)
    monkeypatch.delenv('VISION_MODEL', raising=False)
    monkeypatch.setattr(config, 'get_llm_config', lambda: ('current-plan-key', 'https://plan.example/v1', 'plan-model'))
    assert config.get_vision_config_db() == ('current-plan-key', 'https://plan.example/v1', 'plan-model')


def test_explicit_vision_database_config_still_wins():
    for key, value in [('api_key', 'custom-key'), ('base_url', 'https://custom.example/v1'), ('model', 'custom-model')]:
        config_db.update_config('vision.mimo.' + key, value)
    assert config.get_vision_config_db() == ('custom-key', 'https://custom.example/v1', 'custom-model')


def test_legacy_seed_key_migration_preserves_custom_keys(monkeypatch):
    monkeypatch.setattr(config_db, '_LEGACY_VISION_KEY_SHA256', hashlib.sha256(b'legacy-seed').hexdigest())
    config_db.update_config('vision.mimo.api_key', 'legacy-seed')
    config_db.init_default_configs()
    assert config_db.get_config('vision.mimo.api_key') == ''
    config_db.update_config('vision.mimo.api_key', 'user-configured')
    config_db.init_default_configs()
    assert config_db.get_config('vision.mimo.api_key') == 'user-configured'


def test_authentication_error_is_not_retried(monkeypatch):
    calls = []
    request = httpx.Request('POST', 'https://vision.example/v1/chat/completions')
    response = httpx.Response(401, request=request)
    def reject(*args, **kwargs):
        calls.append(1)
        raise AuthenticationError('Invalid API Key', response=response, body=None)
    monkeypatch.setattr(image_parser, '_resolve_vision', lambda: ('invalid', 'https://vision.example/v1', 'vision'))
    monkeypatch.setattr(image_parser, '_openai_chat', reject)
    monkeypatch.setattr(image_parser.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='鉴权失败.*401'):
        image_parser._call_vision('prompt', 'image', 'png')
    assert calls == [1]


def test_text_fallback_preserves_explicit_percentile_unit():
    from db.valuations import save_valuation, get_latest_valuation
    raw = '指数名称：沪深300\n指数代码：000300\n指标名称：市盈率\n当前值：12.5\n分位点：0.5%'
    parsed = image_parser.ImageParser()._normalize(image_parser.ImageParser._extract_key_value_text(raw))
    save_valuation(parsed)
    assert get_latest_valuation('000300')['percentile'] == 0.5


def test_structured_vision_uses_output_budget_for_extraction(monkeypatch):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    monkeypatch.setattr(image_parser, 'OpenAI', lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    image_parser._openai_chat('Read table', 'image', 'png', 'vision-model', 'https://vision.example/v1', 'test-key')
    assert captured.get('extra_body') == {'thinking': {'type': 'disabled'}}


def test_truncated_table_response_is_rejected_before_json_repair(monkeypatch):
    response = SimpleNamespace(usage=None, choices=[SimpleNamespace(
        finish_reason='length', message=SimpleNamespace(content='{"数据":[{"指数名称":"沪深300"}],'))])
    monkeypatch.setattr(image_parser, 'OpenAI', lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))))
    with pytest.raises(RuntimeError, match='截断'):
        image_parser._openai_chat('Read table', 'image', 'png', 'vision-model', 'https://vision.example/v1', 'test-key')
