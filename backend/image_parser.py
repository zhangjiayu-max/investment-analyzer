"""图片解析服务 — 封装视觉模型调用，输出结构化估值数据"""

import json
import re
import base64
import time
from pathlib import Path
from openai import OpenAI
from config import get_vision_config
from collections import Counter


def extract_dominant_color(image_path: str) -> str | None:
    """
    提取图片的主要背景颜色，用于判断估值区域。

    颜色映射：
    - 绿色/浅绿色 → 低估
    - 黄色/橙色 → 适中
    - 红色/粉红色 → 高估

    返回: "绿色" | "黄色" | "红色" | None
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None

    try:
        img = Image.open(image_path)
        # 缩小图片加速处理
        img = img.resize((100, 100))
        # 转换为 RGB
        if img.mode != 'RGB':
            img = img.convert('RGB')

        pixels = list(img.getdata())
        # 统计颜色频率
        color_counts = Counter(pixels)

        # 定义颜色范围
        green_count = 0
        yellow_count = 0
        red_count = 0
        total_count = 0

        for (r, g, b), count in color_counts.items():
            # 跳过接近白色和黑色的像素
            if r > 230 and g > 230 and b > 230:  # 白色
                continue
            if r < 25 and g < 25 and b < 25:  # 黑色
                continue

            total_count += count

            # 绿色判断：G 通道明显高于 R 和 B
            if g > r * 1.2 and g > b * 1.2 and g > 80:
                green_count += count
            # 黄色判断：R 和 G 通道都高，B 通道低
            elif r > 150 and g > 150 and b < 100:
                yellow_count += count
            # 红色判断：R 通道明显高于 G 和 B
            elif r > g * 1.3 and r > b * 1.3 and r > 100:
                red_count += count

        if total_count == 0:
            return None

        # 计算各颜色占比
        green_pct = green_count / total_count
        yellow_pct = yellow_count / total_count
        red_pct = red_count / total_count

        # 返回占比最高的颜色
        max_pct = max(green_pct, yellow_pct, red_pct)
        if max_pct < 0.1:  # 颜色不明显
            return None

        if max_pct == green_pct:
            return "绿色"
        elif max_pct == yellow_pct:
            return "黄色"
        else:
            return "红色"

    except Exception:
        return None

# 螺丝钉估值表解析 prompt — 提取多指数表格数据
DD_PARSE_PROMPT = """这张图片是螺丝钉发布的指数估值表，包含多个指数的估值数据。

请提取表格中所有指数的数据，输出JSON数组格式：
{
  "更新日期": "2026-05-28",
  "市场温度": 25.5,
  "数据": [
    {
      "指数名称": "沪深300",
      "指数代码": "000300",
      "PE": 12.5,
      "PE百分位": 35.2,
      "PB": 1.35,
      "PB百分位": 28.6,
      "股息率": 2.8,
      "ROE": 10.8,
      "估值状态": "低估",
      "背景颜色": "绿色",
      "背景颜色说明": "绿色表示低估，黄色表示适中，红色表示高估"
    }
  ]
}

注意：
- 如果图片中没有某项数据，对应字段设为null
- 估值状态根据百分位判断：<30%低估，30-70%适中，>70%高估
- 背景颜色请根据指数行的实际背景颜色填写：绿色/浅绿色表示低估，黄色/橙色表示适中，红色/粉红色表示高估
- 如果无法确定背景颜色，设为null
- 市场温度如果没有就设为null
- 更新日期从图片中提取，如果没有就设为null
- 只输出JSON，不要其他文字"""

_api_key, _base_url, _model = get_vision_config()

# 解析 prompt — 提取所有指标类型
PARSE_PROMPT = """提取这张指数估值图的数据。图片可能显示市盈率、市净率、市销率、市现率、股息率、风险溢价中的一种。

输出JSON（没有的字段设为null）：
{
  "指数名称": "...", "指数代码": "...",
  "当前点位": 12345.67, "涨跌幅": 1.23,
  "背景颜色": "绿色",
  "背景颜色说明": "绿色表示低估区域，黄色表示适中区域，红色表示高估区域",
  "市盈率TTM统计指标": { "当前值": 13.96, "分位点": 3.27, "危险值": 21.83, "中位数": 18.88, "机会值": 16.07, "最大值": 35.86, "最小值": 13.23, "平均值": 19.43, "z分数": -1.28 },
  "市净率统计指标": { "当前值": 2.5, "分位点": 50.0, "危险值": 3.5, "中位数": 3.0, "机会值": 2.0, "最大值": 5.0, "最小值": 1.0, "平均值": 2.8, "z分数": 1.5 },
  "市销率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "市现率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "股息率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "风险溢价统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "min": null, "平均值": null, "z分数": null }
}

注意：
- 背景颜色请根据图片的整体背景或主要区域颜色填写：绿色/浅绿色表示低估，黄色/橙色表示适中，红色/粉红色表示高估
- 如果无法确定背景颜色，设为null
- 只输出JSON，不要其他文字。"""


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
        result = self._normalize(data)

        # 6. 如果视觉模型没有返回背景颜色，使用图像处理提取
        if not result.get("background_color"):
            bg_color = extract_dominant_color(image_path)
            if bg_color:
                result["background_color"] = bg_color
                result["background_color_note"] = f"通过图像分析识别：{bg_color}"

        return result

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
            "background_color": data.get("背景颜色"),
            "background_color_note": data.get("背景颜色说明"),
            "raw_json": json.dumps(data, ensure_ascii=False),
        }


class DDImageParser:
    """螺丝钉估值表解析器 — 支持多指数表格数据提取"""

    def __init__(self, model_type: str = "mimo"):
        self.model_type = model_type
        self.client = OpenAI(api_key=_api_key, base_url=_base_url)
        self._model = _model

    def parse(self, image_path: str) -> dict:
        """
        解析螺丝钉估值表图片，返回多指数结构化数据。

        返回:
            {
                "ok": True,
                "update_date": "2026-05-28",
                "market_temperature": 25.5,
                "data": [
                    {"index_name": "...", "index_code": "...", "pe": 12.5, "pe_percentile": 35.2, ...},
                    ...
                ]
            }
        """
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        ext = image_path.rsplit(".", 1)[-1].lower()
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}
        mime = mime_map.get(ext, "jpeg")

        raw = self._call_vision_model(img_b64, mime)
        data = self._extract_json(raw)
        result = self._normalize(data)

        # 如果视觉模型没有返回背景颜色，使用图像处理提取
        if result.get("ok"):
            # 检查是否有任何一个指数没有背景颜色
            need_extract = any(
                not item.get("background_color")
                for item in result.get("data", [])
            )
            if need_extract:
                bg_color = extract_dominant_color(image_path)
                if bg_color:
                    # 为所有没有背景颜色的指数设置默认值
                    for item in result.get("data", []):
                        if not item.get("background_color"):
                            item["background_color"] = bg_color

        return result

    def _call_vision_model(self, img_b64: str, mime: str) -> str:
        """调用视觉模型，最多重试 2 次。"""
        last_err = None
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self._model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": DD_PARSE_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{img_b64}"}},
                        ],
                    }],
                    temperature=0.1,
                    max_tokens=8000,
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
        """从响应中提取 JSON。"""
        raw = raw.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            if len(parts) >= 2:
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            candidate = raw[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 尝试修复截断
                s = re.sub(r',\s*([}\]])', r'\1', candidate)
                open_braces = s.count('{') - s.count('}')
                open_brackets = s.count('[') - s.count(']')
                if open_braces > 0 or open_brackets > 0:
                    s = s.rstrip().rstrip(',')
                    s += ']' * open_brackets + '}' * open_braces
                try:
                    return json.loads(s)
                except json.JSONDecodeError:
                    pass
        return {"raw_response": raw}

    def _normalize(self, data: dict) -> dict:
        """标准化输出格式。"""
        if "raw_response" in data and len(data) == 1:
            return {"ok": False, "error": "解析失败", "raw": data["raw_response"]}

        items = data.get("数据") or []
        normalized = []
        for item in items:
            normalized.append({
                "index_name": item.get("指数名称"),
                "index_code": item.get("指数代码"),
                "pe": item.get("PE"),
                "pe_percentile": item.get("PE百分位"),
                "pb": item.get("PB"),
                "pb_percentile": item.get("PB百分位"),
                "dividend_yield": item.get("股息率"),
                "roe": item.get("ROE"),
                "valuation_status": item.get("估值状态"),
                "background_color": item.get("背景颜色"),
            })

        return {
            "ok": True,
            "update_date": data.get("更新日期"),
            "market_temperature": data.get("市场温度"),
            "data": normalized,
            "count": len(normalized),
            "raw_json": json.dumps(data, ensure_ascii=False),
        }


# 默认实例
parser = ImageParser(model_type="mimo")
dd_parser = DDImageParser(model_type="mimo")
