"""图片解析服务 — 封装视觉模型调用，输出结构化估值数据"""

import json
import re
import base64
import time
import logging
import uuid
from pathlib import Path
from openai import OpenAI
from config import IMAGE_PARSER_MODEL_TYPE
from db.config import get_config_float, get_config_int
from collections import Counter

logger = logging.getLogger(__name__)


def extract_dominant_color(image_path: str) -> str | None:
    """提取图片的主要背景颜色，用于判断估值区域。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(image_path).resize((100, 100))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        pixels = list(img.getdata())
        color_counts = Counter(pixels)
        green_count = yellow_count = red_count = total_count = 0
        for r, g, b in (c for c, _ in color_counts.items()):
            count = color_counts[(r, g, b)]
            if r > 230 and g > 230 and b > 230:
                continue
            if r < 25 and g < 25 and b < 25:
                continue
            total_count += count
            if g > r * 1.2 and g > b * 1.2 and g > 80:
                green_count += count
            elif r > 150 and g > 150 and b < 100:
                yellow_count += count
            elif r > g * 1.3 and r > b * 1.3 and r > 100:
                red_count += count
        if total_count == 0:
            return None
        pc = {green_count / total_count: "绿色", yellow_count / total_count: "黄色", red_count / total_count: "红色"}
        best = max(pc)
        return pc[best] if best > 0.1 else None
    except Exception:
        return None


# ── 配置（运行时从 DB 读取，支持 Ollama/MiMo 切换）──
def _resolve_vision() -> tuple[str, str, str, bool]:
    """每次调用时从 DB 读取视觉模型配置，返回 (api_key, base_url, model, is_ollama)。"""
    from config import get_vision_config_db
    api_key, base_url, model = get_vision_config_db()
    is_ollama = 'localhost' in base_url.lower() or '11434' in base_url
    return api_key, base_url, model, is_ollama


def _ollama_chat(prompt: str, img_b64: str, model: str, base_url: str, trace_id: str = "") -> str:
    """Ollama 原生 API（支持 enable_thinking=False）。"""
    import httpx
    base = base_url.replace('/v1', '').rstrip('/')
    resp = httpx.post(
        f'{base}/api/chat',
        json={
            'model': model,
            'messages': [{'role': 'user', 'content': prompt, 'images': [img_b64]}],
            'options': {'enable_thinking': False},
            'stream': False,
        },
        timeout=get_config_int('llm.timeout_vision', 120),
        trust_env=False,
    )
    data = resp.json()
    # 记录 Ollama token 用量
    prompt_eval = data.get('prompt_eval_count', 0) or 0
    eval_count = data.get('eval_count', 0) or 0
    if prompt_eval or eval_count:
        try:
            from llm_service import _record_token_usage
            _UsageStub = type('_UsageStub', (), {
                'prompt_tokens': prompt_eval,
                'completion_tokens': eval_count,
                'total_tokens': prompt_eval + eval_count,
            })
            _record_token_usage(_UsageStub(), model, "image_parse", trace_id=trace_id)
        except Exception:
            pass
    text = data.get('message', {}).get('content', '') or ''
    if text.strip():
        return text.strip()
    text = data.get('message', {}).get('thinking', '') or ''
    if text.strip():
        return text.strip()
    raise RuntimeError("Ollama 返回空响应")


def _openai_chat(prompt: str, img_b64: str, mime: str, model: str,
                 base_url: str, api_key: str, trace_id: str = "") -> str:
    """OpenAI 兼容接口调用，带 thinking mode 兜底。"""
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=get_config_int('llm.timeout_vision', 120))
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{img_b64}"}},
        ]}],
        temperature=get_config_float('llm.temperature_vision', 0.1),
        max_tokens=get_config_int('llm.max_tokens_vision', 4000),
    )
    if resp.usage:
        try:
            from llm_service import _record_token_usage
            _record_token_usage(resp.usage, resp.model or model, "image_parse", trace_id=trace_id)
        except Exception:
            pass
    text = (resp.choices[0].message.content or '').strip()
    if text:
        return text
    msg = resp.choices[0].message
    for attr in ('thinking', 'reasoning_content'):
        val = getattr(msg, attr, '') or ''
        if val.strip():
            return val.strip()
    if msg.model_extra:
        for v in msg.model_extra.values():
            if isinstance(v, str) and len(v) > 20:
                return v.strip()
    raise RuntimeError("模型返回空响应")


def _call_vision(prompt: str, img_b64: str, mime: str, model: str = "", trace_id: str = "") -> str:
    """调用视觉模型（自动路由 Ollama / OpenAI 兼容，运行时从 DB 读取配置）。"""
    api_key, base_url, db_model, is_ollama = _resolve_vision()
    effective_model = model or db_model
    logger.info(f"[_call_vision] provider={'ollama' if is_ollama else 'openai'}, model={effective_model}, base_url={base_url}")
    last_err = None
    for attempt in range(3):
        try:
            if is_ollama:
                return _ollama_chat(prompt, img_b64, effective_model, base_url, trace_id=trace_id)
            return _openai_chat(prompt, img_b64, mime, effective_model, base_url, api_key, trace_id=trace_id)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    raise last_err or RuntimeError("视觉模型调用失败")


def _extract_json(raw: str) -> dict:
    """从响应中提取 JSON，兼容 markdown、截断、多余文字。"""
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
            pass
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


# ── Prompt ────────────────────────────────────────────

# 螺丝钉估值表（对 8B 小模型友好）
DD_PARSE_PROMPT = """从图片表格读取每个指数，输出 JSON 数组：
{"更新日期":"(YYYY-MM-DD)","市场温度":null,"数据":[{"指数名称":"","PE":null,"PB":null,"股息率":null,"ROE":null,"估值状态":"(低估/适中/高估)","背景颜色":"(绿色/黄色/红色)"}]}
估值状态根据行背景色判断：绿色=低估, 黄色=适中, 红色=高估。只输出 JSON。"""

DD_PARSE_PROMPT_CROP = """读取这张裁剪后的螺丝钉估值表。输出 JSON：
{"数据":[{"指数名称":"","PE":null,"PB":null,"股息率":null,"ROE":null,"背景颜色":"(绿色/橙色/红色)"}]}
只输出 JSON。"""

# 普通估值图（key 与 _normalize 对齐）
PARSE_PROMPT = """从图片读取指数估值数据，输出 JSON：
{"指数名称":"","指数代码":"","当前点位":null,"涨跌幅":null,"背景颜色":"(绿色/黄色/红色)",
"市盈率TTM统计指标":{"当前值":null,"分位点":null,"危险值":null,"中位数":null,"机会值":null,"最大值":null,"最小值":null,"平均值":null,"z分数":null},
"市净率统计指标":{"当前值":null,"分位点":null,"危险值":null,"中位数":null,"机会值":null,"最大值":null,"最小值":null,"平均值":null,"z分数":null},
"股息率统计指标":{"当前值":null}}
背景颜色：绿色=低估、黄色=适中、红色=高估。只看图片实际数据，不编造。只输出 JSON。"""

# 兜底裁剪 Prompt：只关注底部数据表区域（用于整图解析失败时的二次提取）
STATS_CROP_PROMPT = """从这张估值数据表区域读取数据，输出 JSON：
{"指数名称":"","背景颜色":"(绿色/黄色/红色)",
"市盈率TTM统计指标":{"当前值":null,"分位点":null,"危险值":null,"中位数":null,"机会值":null,"最大值":null,"最小值":null,"平均值":null,"z分数":null},
"市净率统计指标":{"当前值":null,"分位点":null,"危险值":null,"中位数":null,"机会值":null,"最大值":null,"最小值":null,"平均值":null,"z分数":null}}
请仔细读取表格中的数字，包括小数点和百分号。不要编造数据，只输出 JSON。"""


# ── ImageParser（普通估值图）─────────────────────────

class ImageParser:
    def __init__(self, model_type: str = "", trace_id: str = ""):
        self.model_type = model_type or IMAGE_PARSER_MODEL_TYPE
        self._trace_id = trace_id

    def parse(self, image_path: str) -> dict:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
        raw = _call_vision(PARSE_PROMPT, img_b64, mime, trace_id=self._trace_id)
        data = _extract_json(raw)
        result = self._normalize(data)

        # 兜底：整图解析无 current_value 时，裁剪底部数据表区域单独提取数值
        if not result.get("current_value"):
            logger.info(f"[ImageParser] 整图解析无 current_value，尝试裁剪底部数据表: {image_path}")
            try:
                from PIL import Image
                import io as _io
                img = Image.open(image_path)
                w, h = img.size
                # 裁剪下半部分（估值数据表区域）
                crop_box = (0, int(h * 0.58), w, h)
                crop = img.crop(crop_box)
                buf = _io.BytesIO()
                crop_fmt = "PNG" if mime == "png" else "JPEG"
                crop.save(buf, format=crop_fmt)
                crop_b64 = base64.b64encode(buf.getvalue()).decode()
                crop_raw = _call_vision(STATS_CROP_PROMPT, crop_b64, mime, trace_id=self._trace_id)
                stats_data = _extract_json(crop_raw)
                # 合并：stats_data 优先填充 current_value / percentile 等字段
                stats = stats_data.get("市盈率TTM统计指标") or stats_data.get("市净率统计指标") or {}
                # 裁剪仍为空时，放大裁剪区域再试一次
                if stats.get("当前值") is None:
                    logger.info(f"[ImageParser] 裁剪标准尺寸仍失败，尝试放大裁剪区域: {image_path}")
                    crop_big = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
                    buf2 = _io.BytesIO()
                    crop_big.save(buf2, format=crop_fmt)
                    crop2_b64 = base64.b64encode(buf2.getvalue()).decode()
                    crop2_raw = _call_vision(STATS_CROP_PROMPT, crop2_b64, mime, trace_id=self._trace_id)
                    stats_data2 = _extract_json(crop2_raw)
                    stats = stats_data2.get("市盈率TTM统计指标") or stats_data2.get("市净率统计指标") or {}
                    if stats.get("当前值") is not None:
                        stats_data = stats_data2  # 使用放大后的结果
                if stats.get("当前值") is not None:
                    logger.info(f"[ImageParser] 裁剪兜底成功: current_value={stats.get('当前值')}")
                    result["current_value"] = stats.get("当前值")
                    result["percentile"] = stats.get("分位点")
                    result["danger_value"] = stats.get("危险值")
                    result["median"] = stats.get("中位数")
                    result["opportunity_value"] = stats.get("机会值")
                    result["max_value"] = stats.get("最大值")
                    result["min_value"] = stats.get("最小值")
                    result["avg_value"] = stats.get("平均值")
                    result["zscore"] = stats.get("z分数")
                    # 根据实际返回的指标类型更新 metric_type
                    if stats_data.get("市净率统计指标", {}).get("当前值") is not None:
                        result["metric_type"] = "市净率"
                    if not result.get("background_color"):
                        result["background_color"] = stats_data.get("背景颜色")
                    # 合并 raw_json
                    result["raw_json"] = json.dumps(stats_data, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"[ImageParser] 裁剪兜底失败: {e}")

        if not result.get("background_color"):
            bg = extract_dominant_color(image_path)
            if bg:
                result["background_color"] = bg
                result["background_color_note"] = f"通过图像分析识别：{bg}"
        return result

    def _normalize(self, data: dict) -> dict:
        if "raw_response" in data and len(data) == 1:
            raw_text = data["raw_response"]
            try:
                inner = json.loads(raw_text)
                if isinstance(inner, dict):
                    data = inner
            except (json.JSONDecodeError, TypeError):
                pass
            if "raw_response" in data:
                re_extracted = _extract_json(raw_text)
                if "raw_response" not in re_extracted:
                    data = re_extracted
            if "raw_response" in data:
                parsed = self._extract_key_value_text(raw_text)
                if parsed:
                    data = parsed

        metric_configs = [
            ("市现率", "市现率统计指标"), ("市销率", "市销率统计指标"),
            ("市净率", "市净率统计指标"), ("股息率", "股息率统计指标"),
            ("风险溢价", "风险溢价统计指标"), ("市盈率", "市盈率TTM统计指标"),
        ]
        value_keys = ["当前值", "当前估值", "当前PE", "当前PB", "PE_TTM", "PB", "当前市盈率", "当前市净率"]

        def _get_current_value(stats: dict):
            for k in value_keys:
                if k in stats and stats[k] is not None:
                    return stats[k]
            return None

        metric_type = "市盈率"
        s = data.get("市盈率TTM统计指标") or {}
        for mt_name, mt_key in metric_configs:
            stats = data.get(mt_key) or {}
            if _get_current_value(stats) is not None:
                if stats.get("当前值") is None:
                    stats["当前值"] = _get_current_value(stats)
                metric_type = mt_name
                s = stats
                break

        return {
            "index_code": data.get("指数代码"), "index_name": data.get("指数名称"),
            "current_point": data.get("当前点位"), "change_pct": data.get("涨跌幅"),
            "metric_type": metric_type, "current_value": s.get("当前值"),
            "percentile": s.get("分位点"), "danger_value": s.get("危险值"),
            "median": s.get("中位数"), "opportunity_value": s.get("机会值"),
            "max_value": s.get("最大值"), "min_value": s.get("最小值"),
            "avg_value": s.get("平均值"), "zscore": s.get("z分数"),
            "background_color": data.get("背景颜色"), "background_color_note": data.get("背景颜色说明"),
            "raw_json": json.dumps(data, ensure_ascii=False),
        }

    @staticmethod
    def _extract_key_value_text(raw_text: str) -> dict | None:
        """从 qwen3-vl thinking 文本兜底提取估值数据字段。"""
        if not raw_text:
            return None
        def _tv(label):
            m = re.search(rf"{label}\s*[：:]\s*([^\n，,。]+)", raw_text)
            return m.group(1).strip() if m else None
        def _nv(label):
            v = _tv(label)
            if v is None:
                return None
            m = re.search(r"[-+]?\d+(?:\.\d+)?", v.replace(",", ""))
            return float(m.group(0)) if m else None

        idx = _tv("指数名称")
        cod = _tv("指数代码")
        if not any([idx, cod]):
            return None

        metric_name = _tv("指标名称")
        metric_key = "市盈率TTM统计指标"
        if metric_name:
            if "净率" in metric_name: metric_key = "市净率统计指标"
            elif "销率" in metric_name: metric_key = "市销率统计指标"
            elif "现率" in metric_name: metric_key = "市现率统计指标"
            elif "股息" in metric_name: metric_key = "股息率统计指标"
            elif "风险" in metric_name: metric_key = "风险溢价统计指标"

        return {
            "指数名称": idx, "指数代码": cod,
            "当前点位": _nv("当前点位"), "涨跌幅": _tv("涨跌幅"),
            "背景颜色": _tv("背景颜色"),
            metric_key: {
                "当前值": _nv("当前值"), "分位点": _nv("分位点"),
                "危险值": _nv("危险值"), "中位数": _nv("中位数"),
                "机会值": _nv("机会值"), "最大值": _nv("最大值"),
                "最小值": _nv("最小值"), "平均值": _nv("平均值"),
                "z分数": _nv("z分数"),
            },
        }


# ── DDImageParser（螺丝钉估值表）────────────────────

class DDImageParser:
    def __init__(self, model_type: str = "", trace_id: str = ""):
        self.model_type = model_type or IMAGE_PARSER_MODEL_TYPE
        self._trace_id = trace_id

    def parse(self, image_path: str) -> dict:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
        raw = _call_vision(DD_PARSE_PROMPT, img_b64, mime, trace_id=self._trace_id)
        data = _extract_json(raw)
        result = self._normalize(data)
        if not result.get("ok") or result.get("count", 0) == 0:
            fallback = self._parse_cropped_regions(image_path, mime)
            if fallback.get("ok") and fallback.get("count", 0) > 0:
                result = fallback
        if result.get("ok"):
            need_bg = any(not item.get("background_color") for item in result.get("data", []))
            if need_bg:
                bg = extract_dominant_color(image_path)
                if bg:
                    for item in result.get("data", []):
                        if not item.get("background_color"):
                            item["background_color"] = bg
        return result

    def _parse_cropped_regions(self, image_path: str, mime: str) -> dict:
        """整张长表失败时裁剪上/中/下三段分别解析后合并。"""
        try:
            from PIL import Image
        except ImportError:
            return {"ok": False, "error": "PIL 不可用", "data": [], "count": 0}
        try:
            img = Image.open(image_path)
            w, h = img.size
            boxes = [
                (0, 0, w, min(h, int(h * 0.36))),
                (0, max(0, int(h * 0.34)), w, min(h, int(h * 0.84))),
                (0, max(0, int(h * 0.82)), w, h),
            ]
            import io
            merged, seen, update_date = [], set(), ""
            for box in boxes:
                buf = io.BytesIO()
                img.crop(box).save(buf, format="PNG" if mime == "png" else "JPEG")
                crop_b64 = base64.b64encode(buf.getvalue()).decode()
                raw = _call_vision(DD_PARSE_PROMPT_CROP, crop_b64, mime, trace_id=self._trace_id)
                partial = self._normalize(_extract_json(raw))
                if partial.get("update_date") and not update_date:
                    update_date = partial["update_date"]
                for item in partial.get("data", []):
                    k = item.get("index_name")
                    if k and k not in seen:
                        seen.add(k)
                        merged.append(item)
            return {"ok": bool(merged), "update_date": update_date, "market_temperature": None,
                    "data": merged, "count": len(merged), "raw_json": json.dumps({"数据": merged}, ensure_ascii=False)}
        except Exception as e:
            return {"ok": False, "error": f"裁剪解析失败: {e}", "data": [], "count": 0}

    def _normalize(self, data: dict) -> dict:
        if "raw_response" in data and len(data) == 1:
            parsed = self._extract_key_value_text(data["raw_response"])
            if parsed:
                data = parsed
            else:
                return {"ok": False, "error": "解析失败", "raw": data["raw_response"]}
        items = data.get("数据") or []
        normalized = []
        for item in items:
            normalized.append({
                "index_name": item.get("指数名称"), "index_code": item.get("指数代码"),
                "pe": self._parse_number(item.get("PE")), "pe_percentile": self._parse_number(item.get("PE百分位")),
                "pb": self._parse_number(item.get("PB")), "pb_percentile": self._parse_number(item.get("PB百分位")),
                "dividend_yield": self._parse_number(item.get("股息率")), "roe": self._parse_number(item.get("ROE")),
                "valuation_status": item.get("估值状态"), "background_color": item.get("背景颜色"),
            })
        raw_date = str(data.get("更新日期") or "").strip()
        nd = self._normalize_date(raw_date)
        nt = self._parse_temperature(data.get("市场温度"))
        return {"ok": True, "update_date": nd, "market_temperature": nt,
                "data": normalized, "count": len(normalized), "raw_json": json.dumps(data, ensure_ascii=False)}

    @staticmethod
    def _extract_key_value_text(raw_text: str) -> dict | None:
        """从 thinking 文本兜底提取螺丝钉表格数据。"""
        if not raw_text:
            return None
        date_match = re.search(r"(\d{8})", raw_text)
        update_date = date_match.group(1) if date_match else ""
        pattern = re.compile(
            r"指数名称\s*[：:]\s*[\"“]?([^\"”\n]+)[\"”]?\s*.{0,80}?PE\s*[：:]\s*([-\d.]+)"
            r".{0,80}?PB\s*[：:]\s*([-\d.]+).{0,80}?股息率\s*[：:]\s*[\"“]?([-\d.]+%?)[\"”]?"
            r".{0,80}?ROE\s*[：:]\s*[\"“]?([-\d.]+%?)[\"”]?(?:.{0,80}?背景颜色\s*[：:]\s*[\"“]?([^\"”\n]+)[\"”]?)?",
            re.S)
        items = []
        for m in pattern.finditer(raw_text):
            color = (m.group(6) or "").strip() or None
            items.append({"指数名称": m.group(1).strip(), "PE": m.group(2), "PB": m.group(3),
                          "股息率": m.group(4), "ROE": m.group(5), "背景颜色": color,
                          "估值状态": "低估" if color and "绿" in color else
                          "高估" if color and "红" in color else
                          "适中" if color and ("黄" in color or "橙" in color) else None})
        return {"更新日期": update_date, "市场温度": None, "数据": items} if items else None

    @staticmethod
    def _normalize_date(raw_date: str) -> str:
        if not raw_date:
            return ""
        if re.match(r'^\d{4}-\d{2}-\d{2}$', raw_date):
            return raw_date
        if re.match(r'^\d{8}$', raw_date):
            return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        m = re.match(r'^(\d{1,2})月(\d{1,2})日?$', raw_date)
        if m:
            return f"{time.localtime().tm_year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.match(r'^(\d{4})年(\d{1,2})月(\d{1,2})日?$', raw_date)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return raw_date

    @staticmethod
    def _parse_temperature(raw_temp) -> float | None:
        if raw_temp is None:
            return None
        m = re.search(r'([\d.]+)', str(raw_temp))
        return float(m.group(1)) if m else None

    @staticmethod
    def _parse_number(raw_value) -> float | None:
        if raw_value is None:
            return None
        m = re.search(r"[-+]?\d+(?:\.\d+)?", str(raw_value).replace(",", ""))
        return float(m.group(0)) if m else None


parser = ImageParser()
dd_parser = DDImageParser()
