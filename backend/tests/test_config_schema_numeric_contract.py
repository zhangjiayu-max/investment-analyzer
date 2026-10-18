"""配置编辑类型应遵循业务消费者，而不是默认值的文本格式。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize(('key', 'value'), [
    ('opportunity.backtest.slippage_bps', '2.5'),
    ('smart_add.base_dca_pct', '4.5'),
    ('daily_advice.base_dca_amount', '500.5'),
])
def test_float_configs_accept_fractional_values_through_api(key, value):
    from db.config import config_schema, get_config_float
    from routers.admin.config import router
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).put('/api/system-config/' + key, json={'value': value})
    assert response.status_code == 200, response.text
    assert get_config_float(key) == float(value)
    assert config_schema(key)['step'] == 'any'


@pytest.mark.parametrize('key', [
    'llm.max_tokens_chat', 'truncation.history_messages', 'daily_advice.max_dca_steps',
])
def test_integer_count_configs_keep_rejecting_fractional_values(key):
    from db.config import config_schema, validate_config_value
    assert config_schema(key)['step'] == 1
    with pytest.raises(ValueError, match='整数'):
        validate_config_value(key, '2.5')
