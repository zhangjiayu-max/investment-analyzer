import json
from pathlib import Path

from services.image_parser import DDImageParser, ImageParser, _extract_json


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def test_call_vision_ollama_bypasses_proxy_and_reads_thinking(monkeypatch):
    """Ollama 路径使用原生 /api/chat，绕过代理，thinking 文本可读。"""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse({
            "message": {
                "content": "",
                "thinking": '{"指数名称":"CS计算机","市净率统计指标":{"当前值":5.18}}',
            }
        })

    monkeypatch.setattr(
        "image_parser._resolve_vision",
        lambda: ("ollama", "http://localhost:11434/v1", "qwen3-vl:8b", True),
    )
    monkeypatch.setattr("httpx.post", fake_post)

    from services.image_parser import _call_vision
    raw = _call_vision("test prompt", "abc123", "png")

    assert json.loads(raw)["指数名称"] == "CS计算机"
    assert calls[0][0] == "http://localhost:11434/api/chat"
    assert calls[0][1]["trust_env"] is False


def test_call_vision_ollama_dd_parser(monkeypatch):
    """DDImageParser 通过 _call_vision 走 Ollama 路径同样生效。"""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse({
            "message": {
                "content": "",
                "thinking": '{"更新日期":"2026-06-26","市场温度":null,"数据":[]}',
            }
        })

    monkeypatch.setattr(
        "image_parser._resolve_vision",
        lambda: ("ollama", "http://localhost:11434/v1", "qwen3-vl:8b", True),
    )
    monkeypatch.setattr("httpx.post", fake_post)

    from services.image_parser import _call_vision
    raw = _call_vision("test prompt", "abc123", "png")

    assert json.loads(raw)["更新日期"] == "2026-06-26"
    assert calls[0][0] == "http://localhost:11434/api/chat"
    assert calls[0][1]["trust_env"] is False


def test_call_vision_mimo_uses_openai_client(monkeypatch):
    """MiMo provider 走 OpenAI 兼容路径。"""
    created_kwargs = {}

    class FakeMsg:
        content = '{"指数名称":"沪深300","市盈率TTM统计指标":{"当前值":12.5}}'
        def __getattr__(self, name):
            if name in ('thinking', 'reasoning_content'):
                return ''
            raise AttributeError(name)
        model_extra = None

    class FakeChoice:
        message = FakeMsg()

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 20
        total_tokens = 30

    class FakeResp:
        choices = [FakeChoice()]
        model = "mimo-v2.5"
        usage = FakeUsage()

    class FakeCompletions:
        def create(self, **kwargs):
            created_kwargs.update(kwargs)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(
        "image_parser._resolve_vision",
        lambda: ("test-key", "https://api.xiaomimimo.com/v1", "mimo-v2.5", False),
    )
    monkeypatch.setattr("image_parser.OpenAI", lambda **kw: FakeClient())

    from services.image_parser import _call_vision
    raw = _call_vision("test prompt", "abc123", "png")

    result = json.loads(raw)
    assert result["指数名称"] == "沪深300"
    assert created_kwargs["model"] == "mimo-v2.5"


def test_image_parser_extracts_leiniuniu_values_from_qwen_thinking_text():
    raw = """
    指数名称：CS计算机
    指数代码：930651.CSI
    当前点位：8140.47
    涨跌幅：+1.89%
    指标名称：市净率
    当前值：5.18
    分位点：83.73%
    危险值：5.11
    中位数：4.35
    机会值：3.77
    最大值：6.36
    最小值：2.66
    平均值：4.39
    z分数：1.10
    """

    parser = ImageParser()
    result = parser._normalize(_extract_json(raw))

    assert result["index_name"] == "CS计算机"
    assert result["index_code"] == "930651.CSI"
    assert result["metric_type"] == "市净率"
    assert result["current_value"] == 5.18
    assert result["percentile"] == 83.73


def test_dd_image_parser_extracts_rows_from_qwen_thinking_text():
    raw = """
    日期是2026年6月26日（20260626）。

    第一行（中证红利低波动）：
    指数名称："中证红利低波动"
    PE: 7.70
    PB: 0.72
    股息率: "5.11%"
    ROE: "9.35%"
    背景颜色: "绿色"

    第二行（沪港深红利低波）：
    指数名称："沪港深红利低波"
    PE: 8.80
    PB: 0.84
    股息率: "4.39%"
    ROE: "9.55%"
    背景颜色: "绿色"
    """

    parser = DDImageParser()
    result = parser._normalize(_extract_json(raw))

    assert result["ok"] is True
    assert result["update_date"] == "2026-06-26"
    assert result["count"] == 2
    assert result["data"][0]["index_name"] == "中证红利低波动"
    assert result["data"][0]["pe"] == 7.70
    assert result["data"][0]["pb"] == 0.72
    assert result["data"][0]["dividend_yield"] == 5.11
    assert result["data"][0]["roe"] == 9.35
    assert result["data"][0]["background_color"] == "绿色"


def test_dd_image_parser_parse_uses_crop_fallback_when_full_image_fails(tmp_path, monkeypatch):
    image_path = tmp_path / "dd.png"
    from PIL import Image
    Image.new("RGB", (572, 1472), "white").save(image_path)

    call_count = [0]

    def fake_call_vision(prompt, img_b64, mime, model="", trace_id=""):
        call_count[0] += 1
        if call_count[0] == 1:
            # 整图解析返回不可结构化文本
            return "这是一段无法结构化的整图推理文本"
        # 裁剪区域返回结构化数据
        return """
        指数名称："中证红利低波动"
        PE: 7.70
        PB: 0.72
        股息率: "5.11%"
        ROE: "9.35%"
        背景颜色: "绿色"
        """

    monkeypatch.setattr("image_parser._call_vision", fake_call_vision)

    result = DDImageParser().parse(str(image_path))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["data"][0]["index_name"] == "中证红利低波动"
    assert result["data"][0]["pe"] == 7.70
    # 整图 1 次 + 3 个裁剪区域 = 4 次调用
    assert call_count[0] == 4


def test_vision_provider_switch(monkeypatch):
    """切换 vision.provider 后 _resolve_vision 返回对应配置。"""
    from config import get_vision_config_db

    provider = ['ollama']
    configs = {
        'vision.ollama.api_key': 'ollama',
        'vision.ollama.base_url': 'http://localhost:11434/v1',
        'vision.ollama.model': 'qwen3-vl:8b',
        'vision.mimo.api_key': 'sk-test',
        'vision.mimo.base_url': 'https://api.xiaomimimo.com/v1',
        'vision.mimo.model': 'mimo-v2.5',
    }

    def fake_get_config(key, default=''):
        if key == 'vision.provider':
            return provider[0]
        return configs.get(key, default)

    monkeypatch.setattr("db.config.get_config", fake_get_config)

    # 初始为 ollama
    api_key, base_url, model = get_vision_config_db()
    assert 'localhost' in base_url
    assert model == 'qwen3-vl:8b'

    # 切换到 mimo
    provider[0] = 'mimo'
    api_key, base_url, model = get_vision_config_db()
    assert 'xiaomimimo' in base_url
    assert model == 'mimo-v2.5'
    assert api_key == 'sk-test'
