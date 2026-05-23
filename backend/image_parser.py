"""图片解析服务 — 封装视觉模型调用，输出结构化估值数据"""

import json
import re
import base64
import time
from pathlib import Path
from openai import OpenAI
from config import get_vision_config

_api_key, _base_url, _model = get_vision_config()

# 解析 prompt — 提取所有指标类型
PARSE_PROMPT = """提取这张指数估值图的数据。图片可能显示市盈率、市净率、市销率、市现率、股息率、风险溢价中的一种。

输出JSON（没有的字段设为null）：
{
  "指数名称": "...", "指数代码": "...",
  "当前点位": 12345.67, "涨跌幅": 1.23,
  "市盈率TTM统计指标": { "当前值": 13.96, "分位点": 3.27, "危险值": 21.83, "中位数": 18.88, "机会值": 16.07, "最大值": 35.86, "最小值": 13.23, "平均值": 19.43, "z分数": -1.28 },
  "市净率统计指标": { "当前值": 2.5, "分位点": 50.0, "危险值": 3.5, "中位数": 3.0, "机会值": 2.0, "最大值": 5.0, "最小值": 1.0, "平均值": 2.8, "z分数": 1.5 },
  "市销率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "市现率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "股息率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "风险溢价统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null }
}
只输出JSON。"""


class ImageParser:
    """图片解析服务 — 支持多模型切换"""

    def __init__(self, model_type: str = "mimo"):
        """
        初始化解析器。

        参数:
            model_type: "mimo" 或 "deepseek"
        """
        self.model_type = model_type
        self.client = OpenAI(api_key=_api_key, base_url=_base_url)
        # 使用配置中的视觉模型，而非硬编码
        self._model = _model

    def parse(self, image_path: str) -> dict:
        """
        解析单张图片，返回结构化估值数据。

        参数:
            image_path: 图片本地路径

        返回:
            结构化数据 dict，包含 index_code, pe_ttm 等字段
        """
        # 1. 读取图片并 base64 编码
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # 2. 获取 MIME 类型
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}
        mime = mime_map.get(ext, "jpeg")

        # 3. 调用视觉模型
        raw = self._call_vision_model(img_b64, mime, self._model)

        # 4. 解析 JSON
        data = self._extract_json(raw)

        # 5. 标准化输出
        return self._normalize(data)

    def _call_vision_model(self, img_b64: str, mime: str, model: str) -> str:
        """调用视觉模型，返回原始响应。最多重试 2 次（共 3 次尝试）。"""
        last_err = None
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PARSE_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{img_b64}"}},
                        ],
                    }],
                    temperature=0.1,
                    max_tokens=4000,
                )
                content = response.choices[0].message.content
                if content and content.strip():
                    return content.strip()
                last_err = RuntimeError("模型返回空响应")
            except Exception as e:
                last_err = e
            if attempt < 2:
                time.sleep(2)
        raise last_err or RuntimeError("视觉模型调用失败")

    def _extract_json(self, raw: str) -> dict:
        """从响应中提取 JSON，兼容 markdown 包裹、截断、多余文字等场景。"""
        raw = raw.strip()
        # 处理 markdown 代码块
        if raw.startswith("```"):
            parts = raw.split("```")
            if len(parts) >= 2:
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 尝试从文本中提取 {...} 部分（AI 偶尔会在 JSON 前后加说明文字）
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            candidate = raw[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
            # 尝试修复截断的 JSON（末尾逗号、未闭合括号）
            repaired = self._repair_json(candidate)
            if repaired:
                return repaired
        # 全部失败时当作 raw_response 保留
        return {"raw_response": raw}

    def _repair_json(self, s: str) -> dict | None:
        """尝试修复截断/格式错误的 JSON。"""
        # 去掉末尾多余逗号
        s = re.sub(r',\s*([}\]])', r'\1', s)
        # 补全未闭合的括号
        open_braces = s.count('{') - s.count('}')
        open_brackets = s.count('[') - s.count(']')
        if open_braces > 0 or open_brackets > 0:
            s = s.rstrip().rstrip(',')
            s += ']' * open_brackets + '}' * open_braces
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    def _normalize(self, data: dict) -> dict:
        """标准化输出格式，适配数据库存储。自动检测指标类型。"""
        # 兜底：如果 data 只是 raw_response 包装，尝试从中提取真实 JSON
        if "raw_response" in data and len(data) == 1:
            raw_text = data["raw_response"]
            # 先尝试直接解析
            try:
                inner = json.loads(raw_text)
                if isinstance(inner, dict):
                    data = inner
            except (json.JSONDecodeError, TypeError):
                pass
            # 直接解析失败，尝试用 _extract_json 重新提取
            if "raw_response" in data:
                re_extracted = self._extract_json(raw_text)
                if "raw_response" not in re_extracted:
                    data = re_extracted
        # 按优先级检测各指标类型
        metric_configs = [
            ("市现率", "市现率统计指标"),
            ("市销率", "市销率统计指标"),
            ("市净率", "市净率统计指标"),
            ("股息率", "股息率统计指标"),
            ("风险溢价", "风险溢价统计指标"),
            ("市盈率", "市盈率TTM统计指标"),
        ]

        # 常见的"当前值"变体 key 名
        _value_keys = ["当前值", "当前估值", "当前PE", "当前PB", "PE_TTM", "PB", "当前市盈率", "当前市净率"]

        def _get_current_value(stats: dict):
            """从指标 dict 中获取当前值，兼容多种 key 命名。"""
            for k in _value_keys:
                if k in stats and stats[k] is not None:
                    return stats[k]
            return None

        metric_type = "市盈率"
        s = data.get("市盈率TTM统计指标") or {}

        for mt_name, mt_key in metric_configs:
            stats = data.get(mt_key) or {}
            if _get_current_value(stats) is not None:
                # 如果使用了变体 key，将其值复制到"当前值"
                if stats.get("当前值") is None:
                    stats["当前值"] = _get_current_value(stats)
                metric_type = mt_name
                s = stats
                break

        return {
            "index_code": data.get("指数代码"),
            "index_name": data.get("指数名称"),
            "current_point": data.get("当前点位"),
            "change_pct": data.get("涨跌幅"),
            "metric_type": metric_type,
            "current_value": s.get("当前值"),
            "percentile": s.get("分位点"),
            "danger_value": s.get("危险值"),
            "median": s.get("中位数"),
            "opportunity_value": s.get("机会值"),
            "max_value": s.get("最大值"),
            "min_value": s.get("最小值"),
            "avg_value": s.get("平均值"),
            "zscore": s.get("z分数"),
            "raw_json": json.dumps(data, ensure_ascii=False),
        }


# 默认实例
parser = ImageParser(model_type="mimo")
