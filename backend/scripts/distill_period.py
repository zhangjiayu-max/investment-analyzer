#!/usr/bin/env python3
"""统一书籍蒸馏脚本 — 清理旧数据 + OCR + 蒸馏 + 质量审核 + 入库 + 向量索引。

完整流程：清理旧数据 → PDF → OCR（多模态）→ Markdown → 蒸馏 → 质量审核 → SQLite + ChromaDB

用法：
    # 完整流程：PDF → OCR → Markdown → 蒸馏 → 入库
    python3 scripts/distill.py full /path/to/book.pdf --name 书名

    # 只做 OCR：PDF → Markdown
    python3 scripts/distill.py ocr /path/to/book.pdf --name 书名

    # 只做蒸馏：从已有 Markdown 文件
    python3 scripts/distill.py distill /path/to/book.md

    # 清理某书的旧数据（SQLite + ChromaDB + 本地文件）
    python3 scripts/distill.py clean --name 书名

    # 查看已蒸馏书籍列表
    python3 scripts/distill.py list

    # 重建某书的 ChromaDB 向量索引
    python3 scripts/distill.py reindex --name 书名

    # 重建所有书籍的向量索引
    python3 scripts/distill.py reindex

    # 试运行（不写入数据库）
    python3 scripts/distill.py full /path/to/book.pdf --name 书名 --dry-run

    # 跳过质量审核
    python3 scripts/distill.py distill /path/to/book.md --skip-review

    # 指定页码范围（OCR 时）
    python3 scripts/distill.py ocr /path/to/book.pdf --name 书名 --start 100 --end 200
"""

import sys
import json
import re
import time
import base64
import argparse
import threading
from pathlib import Path
from io import BytesIO

sys.path.insert(0, str(Path(__file__).parent.parent))

from PyPDF2 import PdfReader
from db.knowledge import add_knowledge, delete_knowledge_by_source, list_knowledge_books
from rag import index_book_knowledge, delete_chroma_by_filter
from config import get_llm_config, get_llm_fallback_config

# ── 多 Key 轮询 LLM 服务（兼容多模型，支持 fallback）──
from openai import OpenAI

# 优先使用 config 中的 LLM 配置，支持 fallback
_primary_cfg = get_llm_config()  # (api_key, base_url, model)
_fallback_cfg = get_llm_fallback_config()  # (api_key, base_url, model) or None
_DISTILL_MODEL = _primary_cfg[2]

# MIMO 套餐 API — 三个 key 轮询，避免并发限流（蒸馏专用）
_DISTILL_KEYS = [
    "tp-ca65d13uw5f06odkihv9336gb69avpj3hjrx6r38og6e8ejw",
]
_DISTILL_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"

# 每个 key 对应一个独立 client
_distill_clients = [OpenAI(api_key=k, base_url=_DISTILL_BASE_URL) for k in _DISTILL_KEYS]
_key_idx = 0
_key_lock = threading.Lock()

# fallback client（DeepSeek 等）
_fallback_client = OpenAI(api_key=_fallback_cfg[0], base_url=_fallback_cfg[1]) if _fallback_cfg else None
_fallback_model = _fallback_cfg[2] if _fallback_cfg else None


def _get_next_client() -> OpenAI:
    """轮询获取下一个 client（线程安全）。"""
    global _key_idx
    with _key_lock:
        client = _distill_clients[_key_idx % len(_distill_clients)]
        _key_idx += 1
        return client


def _get_model_name() -> str:
    return _DISTILL_MODEL


def _call_llm(caller: str = "", model: str = None, **kwargs):
    """三 key 轮询调用 MIMO，自带重试 + fallback 到 DeepSeek。"""
    use_model = model or _DISTILL_MODEL
    client = _get_next_client()

    # 主力 MIMO 轮询重试
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(model=use_model, **kwargs)
            # 记录 token 用量
            if resp.usage:
                from llm_service import _record_token_usage
                _record_token_usage(resp.usage, resp.model or use_model, "distill_period")
            return resp
        except Exception as e:
            if attempt < 3:
                wait = 2 ** attempt
                print(f"  [{caller}] MIMO 调用失败 ({e.__class__.__name__}), 换 key 重试 {attempt+1}/3...")
                time.sleep(wait)
                client = _get_next_client()  # 失败时换下一个 key
            else:
                print(f"  [{caller}] MIMO 全部失败，尝试 fallback...")

    # fallback 到 DeepSeek（如果配置了）
    if _fallback_client and _fallback_model:
        try:
            print(f"  [{caller}] Fallback 到 {_fallback_model}...")
            resp = _fallback_client.chat.completions.create(model=_fallback_model, **kwargs)
            # 记录 token 用量
            if resp.usage:
                from llm_service import _record_token_usage
                _record_token_usage(resp.usage, resp.model or _fallback_model, "distill_period")
            return resp
        except Exception as e:
            print(f"  [{caller}] Fallback 也失败: {e}")
            raise
    raise RuntimeError(f" [{caller}] 所有 LLM 调用失败，无 fallback 配置")

# 输出目录：data/books/
BOOKS_DIR = Path(__file__).parent.parent.parent / "data" / "books"

# 原始书籍目录（优先查找）
DOCUMENT_DIR = Path("/Users/xiaoyuer/projects/document")


# ══════════════════════════════════════════════════════════════
# OCR 模块：PDF → Markdown
# ══════════════════════════════════════════════════════════════

def detect_pdf_type(pdf_path: str) -> tuple[str, int, int]:
    """检测 PDF 类型，返回 (类型, 总页数, 有文字页数)。"""
    reader = PdfReader(pdf_path)
    total = len(reader.pages)

    # 抽样检测
    sample_size = min(50, total)
    step = max(1, total // sample_size)
    has_text = 0

    for i in range(0, total, step):
        text = reader.pages[i].extract_text() or ""
        stripped = text.strip()
        # 阈值 100 字符 + 中文字符占比检查，避免乱码页误判
        if len(stripped) > 100:
            chinese_chars = len(re.findall(r'[一-鿿]', stripped))
            if chinese_chars > 10 or len(stripped) > 300:
                has_text += 1

    sampled = len(range(0, total, step))
    ratio = has_text / sampled if sampled > 0 else 0

    if ratio > 0.7:
        return "text", total, int(total * ratio)
    else:
        return "scanned", total, int(total * ratio)


def extract_text_from_pdf(pdf_path: str) -> str:
    """从文字版 PDF 提取全文。"""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


def _cleanup_text_pdf(text: str, book_title: str) -> str:
    """对文字版 PDF 提取结果进行规则化整理，转为类 Markdown 格式。"""
    lines = text.split('\n')
    cleaned = []
    prev_line = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            prev_line = ""
            continue

        # 跳过明显的页眉页脚（纯数字页码、短文本重复出现）
        if re.match(r'^[\d\s\-–—]+$', stripped) and len(stripped) <= 10:
            continue

        # 检测章节标题并添加 Markdown 格式
        if re.match(r'^[\s]*第[一二三四五六七八九十百千\d]+[章部篇节]', stripped):
            cleaned.append(f"# {stripped}")
            prev_line = ""
            continue

        # 检测节标题（如 "1.1 标题"、"一、标题"）
        # 排除纯数字如 "1.2"（β系数为 1.2），要求小数点后跟中文或字母文字
        if re.match(r'^[\s]*[\d一二三四五六七八九十]+[\.、]\s*[^\d\s]', stripped) and len(stripped) < 50:
            cleaned.append(f"## {stripped}")
            prev_line = ""
            continue

        # 合并断行：如果上一行末尾没有标点，且当前行不以大写/中文标点开头，则合并
        if prev_line and len(prev_line) < 80:
            last_char = prev_line[-1]
            first_char = stripped[0]
            # 上一行末尾没有结束标点，当前行开头不是标题特征
            if last_char not in '。！？；.!?;':
                if not re.match(r'^[\d#\-\*•]', stripped) and not re.match(r'^[A-Z]', first_char):
                    if cleaned:
                        cleaned[-1] = prev_line + stripped
                        prev_line = cleaned[-1]
                        continue

        cleaned.append(stripped)
        prev_line = stripped

    # 合并连续空行
    result = []
    prev_empty = False
    for line in cleaned:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        result.append(line)
        prev_empty = is_empty

    return '\n'.join(result)


def ocr_scanned_pdf(pdf_path: str, book_title: str,
                    start_page: int = None, end_page: int = None,
                    dpi: int = 150, pages_per_chunk: int = 10) -> str:
    """用多模态 LLM 逐页 OCR 扫描版 PDF，返回 Markdown 全文。"""
    from config import get_vision_config

    api_key, base_url, model = get_vision_config()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    print(f"  视觉模型: {model} @ {base_url}")

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    # 确定页码范围
    if start_page is None:
        start_page = 1
    if end_page is None:
        end_page = total_pages
    start_page = max(1, min(start_page, total_pages))
    end_page = max(start_page, min(end_page, total_pages))

    print(f"  页码范围: {start_page}-{end_page} ({end_page - start_page + 1} 页)")

    all_chunks = []
    chunk_texts = []

    for page_num in range(start_page - 1, end_page):
        print(f"  第 {page_num + 1} 页: 转换图片...", end="", flush=True)

        # 转为图片
        from pdf2image import convert_from_path
        images = convert_from_path(
            pdf_path, dpi=dpi,
            first_page=page_num + 1,
            last_page=page_num + 1
        )
        if not images:
            print(" 失败，跳过")
            continue

        img = images[0]
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        # OCR（带重试）
        print(" 识别中...", end="", flush=True)
        text = ""
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                            },
                            {
                                "type": "text",
                                "text": (
                                    "请完整提取这张书籍页面中的所有文字内容。\n"
                                    "要求：\n"
                                    "1. 保持原文段落结构，不要遗漏任何正文\n"
                                    "2. 严格忽略页码、页眉页脚、装饰性分隔线\n"
                                    "3. 保留标题层级（用 # 标记，如 # 第一章、## 1.1 节）\n"
                                    "4. 公式用 LaTeX 格式（行内 $...$，行间 $$...$$）\n"
                                    "5. 表格用 Markdown 表格格式，尽量还原行列结构\n"
                                    "6. 图片/图表用文字描述其含义（如『图1：XX趋势图』）\n"
                                    "7. 注释/脚注保留在正文对应位置，用〔注〕标记\n"
                                    "8. 只输出文字内容，无其他说明"
                                )
                            }
                        ]
                    }],
                    max_tokens=4000,
                )
                text = resp.choices[0].message.content or ""
                if len(text) > 20:
                    # 记录 token 用量
                    if resp.usage:
                        from llm_service import _record_token_usage
                        _record_token_usage(resp.usage, resp.model or model, "distill_period_ocr")
                    break
            except Exception as e:
                if attempt < 2:
                    print(f" 重试{attempt + 1}...", end="", flush=True)
                    time.sleep(1)
                else:
                    print(f" ✗ ({e})")
                    break

        if text and len(text) > 20:
            chunk_texts.append(text)
            print(f" ✓ ({len(text)} 字)")
        elif text:
            print(f" ⚠ 结果过短 ({len(text)} 字)，跳过)")
        else:
            print(" ✗ 识别失败")

        # 每 pages_per_chunk 页合并一次
        if len(chunk_texts) >= pages_per_chunk:
            merged = _merge_pages(chunk_texts, book_title, client, model)
            all_chunks.append(merged)
            chunk_texts = []
            print(f"  [合并] 已合并 {pages_per_chunk} 页 ({len(merged)} 字)")

        time.sleep(0.3)

    # 合并剩余页
    if chunk_texts:
        merged = _merge_pages(chunk_texts, book_title, client, model)
        all_chunks.append(merged)

    return "\n\n---\n\n".join(all_chunks)


def _merge_pages(pages_text: list[str], book_title: str, client, model: str) -> str:
    """将多页文本合并整理成连贯的 Markdown。"""
    combined = "\n\n---\n\n".join(pages_text)
    if len(combined) < 200:
        return combined

    prompt = f"""请将以下多页书籍《{book_title}》的内容整理成连贯的 Markdown 文本。

要求：
1. 合并跨页的段落，去除断句和 hyphenation（如 "invest-ment" → "investment"）
2. 识别并重建章节结构（# 章标题、## 节标题、### 小节）
3. 保留所有重要内容：概念定义、公式推导、数据表格、历史案例、操作步骤
4. 用 Markdown 格式组织（# 标题、## 子标题、- 列表、| 表格、```代码块 等）
5. 去除页眉页脚残留、页码、重复水印
6. 保留原文含义，不要添加个人评论或修改原意
7. 如果有表格，用 Markdown 表格格式，尽量还原完整行列
8. 如果内容明显是目录或索引页，标注为 `<!-- 目录页 -->` 并精简保留

## 原始内容：
{combined}

请输出整理后的 Markdown 文本："""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=8000,
        )
        # 记录 token 用量
        if resp.usage:
            from llm_service import _record_token_usage
            _record_token_usage(resp.usage, resp.model or model, "distill_period_merge")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  合并失败: {e}")
        return combined


def pdf_to_markdown(pdf_path: str, book_title: str,
                    start_page: int = None, end_page: int = None) -> str:
    """PDF 转 Markdown（自动选择方式）。返回 Markdown 内容，同时保存 .md 文件。"""
    pdf_path = Path(pdf_path)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    md_file = BOOKS_DIR / f"{book_title}.md"

    # 检查是否已有缓存
    if md_file.exists():
        existing = md_file.read_text(encoding="utf-8")
        if len(existing) > 1000:
            print(f"  发现已有 Markdown 文件: {md_file} ({len(existing)} 字)")
            print(f"  如需重新提取，请删除该文件后重试")
            return existing

    # 检测类型
    pdf_type, total_pages, text_pages = detect_pdf_type(str(pdf_path))
    print(f"  PDF 类型: {pdf_type} ({total_pages} 页, ~{text_pages} 页有文字)")

    if pdf_type == "text":
        print(f"  提取文字...")
        raw_text = extract_text_from_pdf(str(pdf_path))
        print(f"  整理格式...")
        full_text = _cleanup_text_pdf(raw_text, book_title)
        print(f"  提取完成: {len(raw_text)} 字 → 整理后 {len(full_text)} 字")
    else:
        print(f"  使用多模态 OCR...")
        full_text = ocr_scanned_pdf(str(pdf_path), book_title, start_page, end_page)

    # 保存 Markdown 文件
    header = f"# {book_title}\n\n"
    header += f"> 由 LLM 视觉识别自动生成\n"
    header += f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"

    md_file.write_text(header + full_text, encoding="utf-8")
    print(f"  已保存 Markdown: {md_file} ({len(full_text)} 字)")

    return full_text


# ══════════════════════════════════════════════════════════════
# 蒸馏模块：Markdown → 知识点
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# 书籍类型感知配置
# ══════════════════════════════════════════════════════════════

BOOK_TYPE_MAP = {
    # 实战操作型：具体步骤、量化规则、选股标准
    "彼得·林奇的成功投资": "practical",
    "手把手教你读财报": "practical",
    "债券投资实战": "practical",
    # 理念框架型：核心原则、数学推导、历史验证
    "聪明的投资者": "framework",
    "投资最重要的事": "framework",
    "投资要义": "framework",
    "投资中最简单的事": "framework",
    # 资产配置型：配置比例、资产特性、跨资产比较
    "资产配置的艺术": "allocation",
    "漫步华尔街": "allocation",
    "共同基金常识": "allocation",
    "指数基金投资指南": "allocation",
    "定投十年财务自由": "allocation",
    # 市场周期型：周期指标、阶段判断
    "周期": "cycle",
    # 行为心理型：认知偏差、决策陷阱
    "思考，快与慢": "behavioral",
    # 基金/黄金科普型
    "黄金投资新时代": "commodity",
}

BOOK_TYPES = {
    "practical": {
        "name": "实战操作型",
        "chunk_size": 3000,
        "min_content_length": 250,
        "review_importance_threshold": 7,
        "review_target_deletion": "30-50%",
        "extract_focus": "具体步骤、量化规则、选股/择时标准、可执行的交易规则、实战案例",
        "extract_examples": [
            '有具体步骤的投资策略（如"当XX指标超过YY时，分3批买入"）',
            '量化的公式/阈值/参数（如"久期缺口控制在±0.5年以内"）',
            '可直接执行的交易规则（如"止损线设为成本价的-5%"）',
        ],
    },
    "framework": {
        "name": "理念框架型",
        "chunk_size": 6000,
        "min_content_length": 300,
        "review_importance_threshold": 6,
        "review_target_deletion": "20-40%",
        "extract_focus": "核心投资原则、数学推导、历史验证数据、反直觉结论、适用边界",
        "extract_examples": [
            '核心原则 + 历史验证数据（如"安全边际要求价格低于内在价值至少30%，格雷厄姆回测1929-1956年数据..."）',
            '数学推导或量化框架（如"市场温度指标 = PE百分位×0.4 + PB百分位×0.3 + ..."）',
            '反直觉结论 + 实证支撑（如"频繁调仓反而降低收益，年化换手率<30%最优，来源：XX研究"）',
        ],
    },
    "allocation": {
        "name": "资产配置型",
        "chunk_size": 5000,
        "min_content_length": 250,
        "review_importance_threshold": 6,
        "review_target_deletion": "20-40%",
        "extract_focus": "配置比例、资产类别特性、再平衡规则、跨资产长期数据、风险收益特征",
        "extract_examples": [
            '具体配置比例 + 历史回测（如"60/40组合在1926-2020年年化收益8.7%，最大回撤30.7%"）',
            '资产类别特性对比（如"股票长期跑赢债券，但短期波动是债券的3倍"）',
            '再平衡规则（如"每年一次或偏离目标5%时触发"）',
        ],
    },
    "cycle": {
        "name": "市场周期型",
        "chunk_size": 5000,
        "min_content_length": 250,
        "review_importance_threshold": 6,
        "review_target_deletion": "20-40%",
        "extract_focus": "周期指标、阶段判断标准、各阶段应对策略、历史周期案例",
        "extract_examples": [
            '周期阶段判断指标（如"信贷周期中，当垃圾债利差<3%时处于周期顶部区域"）',
            '历史周期案例 + 数据（如"2000年互联网泡沫，NASDAQ PE达175倍，随后下跌78%"）',
        ],
    },
    "behavioral": {
        "name": "行为心理型",
        "chunk_size": 4000,
        "min_content_length": 200,
        "review_importance_threshold": 6,
        "review_target_deletion": "20-40%",
        "extract_focus": "认知偏差定义、投资决策中的具体表现、实证数据、规避方法",
        "extract_examples": [
            '认知偏差 + 投资场景（如"锚定效应：投资者过度关注买入价而非当前内在价值"）',
            '实验数据支撑（如"卡尼曼实验显示，亏损100元的痛苦≈盈利250元的快乐"）',
        ],
    },
    "commodity": {
        "name": "商品科普型",
        "chunk_size": 3000,
        "min_content_length": 250,
        "review_importance_threshold": 7,
        "review_target_deletion": "30-50%",
        "extract_focus": "价格驱动因素、投资工具对比、配置比例、风险特征",
        "extract_examples": [
            '价格驱动因素 + 数据（如"黄金与实际利率负相关，r=-0.82，1971-2020"）',
            '投资工具对比（如"实物黄金ETF费率0.40%，纸黄金无托管费但存在银行信用风险"）',
        ],
    },
}


def _detect_book_type(book_title: str, manual_type: str = None) -> str:
    """检测或返回书籍类型。"""
    if manual_type and manual_type in BOOK_TYPES:
        return manual_type
    return BOOK_TYPE_MAP.get(book_title, "practical")


def _pre_check_source_quality(text: str, book_title: str) -> dict:
    """源文本质量预检。"""
    lines = text.split('\n')
    total_lines = len([l for l in lines if l.strip()])
    header_lines = sum(1 for l in lines if re.match(r'^#{1,4}\s+', l.strip()))
    list_lines = sum(1 for l in lines if re.match(r'^\s*[-*\d]', l.strip()))
    short_paragraphs = sum(1 for l in lines if 0 < len(l.strip()) < 50)

    avg_para_len = len(text) / max(total_lines, 1)

    warnings = []
    if header_lines > total_lines * 0.3:
        warnings.append(f"标题行占比过高({header_lines}/{total_lines})，疑似目录/大纲")
    if avg_para_len < 80:
        warnings.append(f"平均段落长度仅{avg_para_len:.0f}字，源文本可能过于碎片化")
    if short_paragraphs > total_lines * 0.5:
        warnings.append(f"短段落占比过高，内容可能缺乏深度论述")

    return {
        "warnings": warnings,
        "avg_para_len": avg_para_len,
        "header_ratio": header_lines / max(total_lines, 1),
    }


def split_into_chunks(text: str, max_chars: int = 3000) -> list[dict]:
    """将文本分割成适合 LLM 处理的块。优先按章节标题切分。"""
    # 尝试按章节标题分割（支持 # 和 章/节）
    sections = []
    current_title = "前言"
    current_lines = []

    for line in text.split('\n'):
        # 检测 Markdown 标题（1-4级）或中文章节标题
        is_header = False
        if re.match(r'^#{1,4}\s+', line):
            is_header = True
            current_title = line.lstrip('#').strip()
        elif re.match(r'^[\s]*(第[一二三四五六七八九十百千\d]+[章部篇节].*)$', line):
            is_header = True
            current_title = line.strip()

        if is_header:
            if current_lines:
                sections.append({
                    "title": current_title,
                    "content": '\n'.join(current_lines)
                })
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "title": current_title,
            "content": '\n'.join(current_lines)
        })

    # 如果没有章节结构，按段落块切分
    if len(sections) <= 1:
        sections = []
        paragraphs = text.split('\n\n')
        current_chunk = []
        current_len = 0
        chunk_idx = 1

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if current_len + len(para) > max_chars and current_chunk:
                sections.append({
                    "title": f"第 {chunk_idx} 部分",
                    "content": '\n\n'.join(current_chunk)
                })
                chunk_idx += 1
                current_chunk = []
                current_len = 0
            current_chunk.append(para)
            current_len += len(para)

        if current_chunk:
            sections.append({
                "title": f"第 {chunk_idx} 部分",
                "content": '\n\n'.join(current_chunk)
            })

    # 将大块再切分成小块（章节完整性保护：不跨 # 一级标题切分）
    chunks = []
    for section in sections:
        content = section["content"].strip()
        if len(content) < 100:
            continue

        if len(content) <= max_chars:
            chunks.append({
                "title": section["title"],
                "content": content
            })
        else:
            # 按段落切分，但保护子章节结构（不跨 ## 切分）
            lines = content.split('\n')
            current_chunk = []
            current_len = 0

            for line in lines:
                is_sub_header = re.match(r'^##\s+', line.strip())
                # 如果下一个是子标题，且当前 chunk 已有内容，优先在此切分
                if is_sub_header and current_chunk and current_len > max_chars * 0.5:
                    chunks.append({
                        "title": section["title"],
                        "content": '\n'.join(current_chunk)
                    })
                    current_chunk = [line]
                    current_len = len(line)
                    continue

                if current_len + len(line) > max_chars and current_chunk:
                    chunks.append({
                        "title": section["title"],
                        "content": '\n'.join(current_chunk)
                    })
                    current_chunk = []
                    current_len = 0
                current_chunk.append(line)
                current_len += len(line)

            if current_chunk:
                chunks.append({
                    "title": section["title"],
                    "content": '\n'.join(current_chunk)
                })

    return chunks


def _parse_json_robust(text: str):
    """鲁棒的 JSON 解析，处理 LLM 输出中的常见格式问题。"""
    text = text.strip()
    # 去掉 markdown 代码块
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 [...]
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if not match:
        return None

    raw = match.group()

    # 逐个修复 JSON 问题
    fixes = [
        raw,
        raw.replace('\n', '\\n'),
        raw.replace('\r\n', '\\n').replace('\r', '\\n'),
    ]

    # 更激进的修复
    try:
        def fix_string_value(m):
            s = m.group(0)
            inner = s[1:-1]
            inner = inner.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            inner = re.sub(r'(?<!\\)"', '\\"', inner)
            return '"' + inner + '"'

        fixed = re.sub(r'"(?:[^"\\]|\\.)*"', fix_string_value, raw)
        fixes.append(fixed)
    except Exception:
        pass

    for fix in fixes:
        try:
            return json.loads(fix)
        except json.JSONDecodeError:
            continue

    return None


def _build_extract_prompt(chunk: str, chapter_title: str, book_title: str, book_type: str) -> str:
    """根据书籍类型构建提取 prompt。"""
    tcfg = BOOK_TYPES[book_type]
    focus = tcfg["extract_focus"]
    examples = "\n".join(f"- {e}" for e in tcfg["extract_examples"])
    min_len = tcfg["min_content_length"]
    max_len = 600 if book_type in ("framework", "allocation") else 500

    return f"""你是金融投资知识提取专家。请从以下书籍内容中提取**最核心、最有价值**的投资知识点。

## 书名：{book_title}
## 章节：{chapter_title}
## 书籍类型：{tcfg['name']}

## 提取原则（严格遵守）

**宁缺毋滥**：宁可少提，不可滥提。

### 本类型书籍的提取重点
{focus}

### 必须提取的内容示例（至少满足一项）
{examples}

### 必须排除的内容（发现即删除）
- ❌ 纯概念定义而无数据/案例支撑（如"什么是久期"但无展开）
- ❌ 常识性内容（如"投资有风险"、"要分散投资"）
- ❌ 笼统建议（如"关注市场动态"、"做好风险管理"）
- ❌ 历史背景介绍（如"我国债券市场发展历程"）
- ❌ 作者个人经历/传记片段
- ❌ 与其他知识点高度重复的内容

### 内容质量要求
- 每个知识点 {min_len}-{max_len} 字，**必须有具体数字、公式、步骤或案例**
- 独立完整，不依赖上下文即可理解
- 避免空洞修饰词（"非常"、"极其"、"显著"），用数据代替形容词
- **标题必须包含核心概念/关键词**（如"安全边际30%阈值"而非"投资原则"）

### 分类标签
- strategy: 具体的买卖策略、仓位管理、择时方法
- indicator: 技术指标、估值指标、市场情绪指标
- principle: 经过验证的投资原则、交易规则
- case: 历史案例、经典交易、数据规律
- psychology: 交易心理、行为金融学洞见
- formula: 计算公式、量化模型

### 输出格式（JSON 数组）
[
  {{"title": "精炼的标题（10-25字，含核心概念）", "category": "strategy|indicator|principle|case|psychology|formula", "content": "{min_len}-{max_len}字的详细内容，必须包含具体数字/公式/步骤/案例", "keywords": ["关键词1", "关键词2", "关键词3"], "importance": 1-10, "reason": "一句话说明为什么这个知识点值得提取"}},
  ...
]

**重要**：如果本段内容没有值得提取的高质量知识点，直接返回空数组 []。

## 文本内容：
{chunk}

只输出 JSON 数组，无其他文字。"""


def extract_knowledge(chunk: str, chapter_title: str, book_title: str, book_type: str = "practical") -> list[dict]:
    """用 LLM 从文本块中提取高质量知识点（支持书籍类型感知）。"""
    prompt = _build_extract_prompt(chunk, chapter_title, book_title, book_type)
    tcfg = BOOK_TYPES[book_type]
    min_len = tcfg["min_content_length"]

    try:
        response = _call_llm(
            caller="distill",
            model=_DISTILL_MODEL,
            messages=[
                {"role": "system", "content": "你是金融投资知识提取专家，只输出 JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=8000,
        )
        content = response.choices[0].message.content or ""

        content = content.strip()
        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

        result = _parse_json_robust(content)
        if result is None:
            print(f"    ⚠️ 提取解析失败: LLM 响应不是有效 JSON ({len(content)} 字符)")
            if len(content) > 50:
                print(f"      响应前 100 字: {content[:100]}...")
            return []

        if not isinstance(result, list):
            print(f"    ⚠️ 提取解析失败: 返回类型 {type(result).__name__}，期望 list")
            return []

        # 质量过滤（提取阶段就严格过滤，减少无效token消耗）
        valid = []
        for item in result:
            if not isinstance(item, dict):
                continue
            if not item.get("title") or not item.get("content"):
                continue
            if len(item["content"]) < min_len:
                continue
            if len(item["title"]) < 3:
                continue
            # 标题质量检查：必须包含具体概念，不能是泛泛的"投资原则"、"市场分析"
            title = item["title"].strip()
            vague_titles = {"投资原则", "市场分析", "投资建议", "投资策略", "投资方法", "市场观点"}
            if title in vague_titles or re.match(r'^[\s]*第[一二三四五六七八九十百千\d]+[章部篇节]', title):
                continue
            # 验证 category
            valid_categories = {"strategy", "indicator", "principle", "case", "psychology", "formula"}
            if item.get("category") not in valid_categories:
                item["category"] = "principle"
            valid.append(item)

        return valid

    except Exception as e:
        print(f"  LLM 提取失败: {e}")
        return []


def deduplicate(knowledge: list[dict]) -> list[dict]:
    """基于标题+关键词去重。"""
    if not knowledge:
        return []

    unique = []
    seen_keys = set()

    for k in knowledge:
        title = k["title"].strip()
        keywords = k.get("keywords", [])
        keyword_str = "|".join(sorted(keywords[:3])) if keywords else ""
        key = f"{title[:20]}|{keyword_str}"

        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(k)

    return unique


def deduplicate_semantic(knowledge: list[dict]) -> list[dict]:
    """基于语义相似度去重（用 LLM 识别重复组）。"""
    if len(knowledge) <= 10:
        return knowledge

    summaries = []
    for i, k in enumerate(knowledge):
        keywords = k.get("keywords", [])
        kw_str = ", ".join(keywords[:5]) if keywords else "无"
        summaries.append(f"[{i}] {k['title']} | 分类: {k.get('category', '?')} | 关键词: {kw_str}")

    summary_text = "\n".join(summaries)

    prompt = f"""请识别以下知识点中的**重复或高度相似**的内容，返回需要删除的索引号。

## 知识点列表：
{summary_text}

## 判断标准
- 两个知识点讨论同一个概念/策略/公式，只是表述不同 → 标记为重复
- 两个知识点虽然关键词相似，但内容侧重点不同 → 不算重复
- 保留**内容最详细、最完整**的版本，删除简略版本

## 输出格式（JSON 数组）
只返回需要删除的索引号数组，如 [2, 5, 8]
如果没有重复，返回 []

只输出 JSON 数组，无其他文字。"""

    try:
        response = _call_llm(
            caller="distill_dedup",
            model=_DISTILL_MODEL,
            messages=[
                {"role": "system", "content": "你是知识去重专家，只输出 JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=500,
        )
        content = response.choices[0].message.content or ""
        content = content.strip()

        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

        to_remove = _parse_json_robust(content)
        if isinstance(to_remove, list):
            remove_set = set(i for i in to_remove if isinstance(i, int) and 0 <= i < len(knowledge))
            result = [k for i, k in enumerate(knowledge) if i not in remove_set]
            print(f"  语义去重: 删除 {len(remove_set)} 条重复内容")
            return result
    except Exception as e:
        print(f"  语义去重失败（回退到基础去重）: {e}")

    return knowledge


def _review_batch(batch: list[dict], book_title: str, batch_idx: int, total_batches: int, book_type: str = "practical") -> list[dict]:
    """审核一批知识点（内部辅助函数），支持按书籍类型调整审核标准。"""
    tcfg = BOOK_TYPES[book_type]
    min_importance = tcfg["review_importance_threshold"]
    target_deletion = tcfg["review_target_deletion"]
    min_len = tcfg["min_content_length"]
    max_len = 600 if book_type in ("framework", "allocation") else 500

    items_text = []
    for i, k in enumerate(batch):
        items_text.append(
            f"[{i}] 【{k['title']}】\n分类: {k.get('category', '?')}\n"
            f"重要性: {k.get('importance', '?')}/10\n内容: {k['content'][:800]}"
        )

    all_items = "\n\n---\n\n".join(items_text)

    prompt = f"""你是金融投资知识质量审核专家。请对以下从《{book_title}》中提取的知识点进行**最终审核和优化**。

## 书籍类型：{tcfg['name']}

## 审核任务

1. **删除低质量内容**（删除率应达 {target_deletion}）：
   - 删除：纯概念解释而无数据/案例支撑
   - 删除：常识性内容、笼统建议
   - 删除：与其他知识点高度重复的内容
   - 删除：缺乏具体数字/公式/步骤/案例的内容
   - 删除：重要性低于 {min_importance} 分的内容

2. **合并相似内容**：
   - 如果多个知识点讨论同一概念，合并为一个最完整的版本
   - 合并后保留最详细的内容，补充其他版本的独特信息

3. **重新评分**：
   - importance 重新评估（1-10），{min_importance}分以下的删除
   - 理念框架型书籍允许抽象原则，但必须有数据/案例支撑
   - 实战操作型书籍必须有可执行的具体步骤或参数

4. **优化内容**：
   - 如果某个知识点内容不够具体，补充关键细节
   - 确保每个知识点都包含具体数字、公式、步骤或案例
   - 标题必须包含核心概念（如"安全边际30%阈值"优于"投资原则"）

## 知识点列表（第 {batch_idx + 1}/{total_batches} 批，共 {len(batch)} 条）：

{all_items}

## 输出格式（JSON 数组）
[
  {{"title": "优化后的标题（10-25字，含核心概念）", "category": "strategy|indicator|principle|case|psychology|formula", "content": "优化后的内容（{min_len}-{max_len}字）", "keywords": ["关键词1", "关键词2", "关键词3"], "importance": {min_importance}-10}},
  ...
]

**重要**：
- 只输出审核通过的知识点，不通过的直接删除
- 每个保留的知识点必须有 {min_importance} 分以上的重要性
- 本批保留数量控制在原数量的 50-80%

只输出 JSON 数组，无其他文字。"""

    try:
        print(f"    审核批次 {batch_idx + 1}/{total_batches} ({len(batch)} 条)...")
        response = _call_llm(
            caller="distill_review",
            model=_DISTILL_MODEL,
            messages=[
                {"role": "system", "content": "你是金融投资知识质量审核专家，只输出 JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=8000,
        )
        content = response.choices[0].message.content or ""
        content = content.strip()

        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

        result = _parse_json_robust(content)
        if not isinstance(result, list):
            print(f"    批次 {batch_idx + 1} 解析失败，保留原始数据")
            print(f"      LLM 响应长度: {len(content)} 字符")
            print(f"      响应前 200 字: {content[:200]}...")
            if len(content) > 1800:
                print(f"      ⚠️ 响应可能被截断（{len(content)} 字符）")
            return batch

        reviewed = []
        for item in result:
            if not isinstance(item, dict):
                continue
            if not item.get("title") or not item.get("content"):
                continue
            if len(item["content"]) < min_len:
                continue
            importance = item.get("importance", min_importance)
            if not isinstance(importance, (int, float)) or importance < min_importance:
                continue
            valid_categories = {"strategy", "indicator", "principle", "case", "psychology", "formula"}
            if item.get("category") not in valid_categories:
                item["category"] = "principle"
            reviewed.append(item)

        print(f"    批次 {batch_idx + 1}: {len(batch)} → {len(reviewed)} 条")
        return reviewed

    except Exception as e:
        print(f"    批次 {batch_idx + 1} 审核失败（保留原始数据）: {type(e).__name__}: {e}")
        return batch


def review_knowledge(knowledge: list[dict], book_title: str, book_type: str = "practical") -> list[dict]:
    """LLM 质量审核：分批处理避免 token 超限，合并重复、删除低质量、重新评分。"""
    if not knowledge:
        return []

    # 如果知识点较少，直接审核；否则分批（每批最多 20 条）
    batch_size = 20
    if len(knowledge) <= batch_size:
        return _review_batch(knowledge, book_title, 0, 1, book_type)

    total_batches = ((len(knowledge) - 1) // batch_size) + 1
    print(f"  知识点较多（{len(knowledge)} 条），分 {total_batches} 批审核...")
    all_reviewed = []

    for i in range(0, len(knowledge), batch_size):
        batch = knowledge[i:i + batch_size]
        reviewed = _review_batch(batch, book_title, i // batch_size, total_batches, book_type)
        all_reviewed.extend(reviewed)
        time.sleep(0.3)

    # 分批审核后，如果总量仍较多，再进行一次跨批次去重
    if len(all_reviewed) > batch_size:
        print(f"  跨批次去重...")
        all_reviewed = deduplicate(all_reviewed)
        if len(all_reviewed) > batch_size:
            all_reviewed = deduplicate_semantic(all_reviewed)

    print(f"  审核完成: {len(knowledge)} → {len(all_reviewed)} 条（删除 {len(knowledge) - len(all_reviewed)} 条低质量内容）")
    return all_reviewed if all_reviewed else knowledge


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def distill_from_text(text: str, book_title: str,
                      dry_run: bool = False, skip_review: bool = False,
                      book_type: str = None, concurrency: int = 1) -> int:
    """从文本进行三阶段蒸馏，返回保存条数。

    Args:
        text: 书籍全文
        book_title: 书名
        dry_run: 试运行
        skip_review: 跳过审核
        book_type: 书籍类型（practical/framework/allocation/cycle/behavioral/commodity）
        concurrency: 并发提取数（默认1，避免API限流）
    """
    # 检测书籍类型
    detected_type = _detect_book_type(book_title, book_type)
    tcfg = BOOK_TYPES[detected_type]
    print(f"  书籍类型: {tcfg['name']} | chunk_size={tcfg['chunk_size']} | min_len={tcfg['min_content_length']}")

    # 源文本质量预检
    print(f"\n  [预检] 源文本质量检查...")
    quality = _pre_check_source_quality(text, book_title)
    if quality["warnings"]:
        for w in quality["warnings"]:
            print(f"    ⚠ {w}")
    else:
        print(f"    ✓ 源文本质量正常（平均段落{quality['avg_para_len']:.0f}字）")

    # 分块（按类型使用不同大小）
    chunks = split_into_chunks(text, max_chars=tcfg["chunk_size"])
    print(f"\n  分割为 {len(chunks)} 个文本块（chunk_size={tcfg['chunk_size']}）")

    # 第一阶段：分块提取
    print(f"\n  [第一阶段] 分块提取知识点...")
    all_knowledge = []

    if concurrency > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        valid_chunks = [(i, c) for i, c in enumerate(chunks) if len(c["content"].strip()) >= 100]
        skipped = len(chunks) - len(valid_chunks)
        if skipped:
            print(f"  跳过 {skipped} 个过短的块")

        def _extract_one(args):
            idx, chunk = args
            knowledge = extract_knowledge(chunk["content"], chunk["title"], book_title, detected_type)
            return idx, chunk["title"], knowledge

        completed = 0
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(_extract_one, item): item for item in valid_chunks}
            for future in as_completed(futures):
                idx, title, knowledge = future.result()
                completed += 1
                all_knowledge.extend(knowledge)
                print(f"  [{completed}/{len(valid_chunks)}] {title[:40]}... → {len(knowledge)} 个知识点")
                for k in knowledge:
                    print(f"    [{k.get('category', '?')}] {k['title'][:50]} ({len(k['content'])}字)")
    else:
        for i, chunk in enumerate(chunks):
            print(f"  [{i+1}/{len(chunks)}] {chunk['title'][:40]}...", end="", flush=True)

            if len(chunk["content"].strip()) < 100:
                print(" 跳过（太短）")
                continue

            knowledge = extract_knowledge(chunk["content"], chunk["title"], book_title, detected_type)
            all_knowledge.extend(knowledge)
            print(f" → {len(knowledge)} 个知识点")

            for k in knowledge:
                print(f"    [{k.get('category', '?')}] {k['title'][:50]} ({len(k['content'])}字)")

            time.sleep(0.5)

    print(f"\n  第一阶段提取完成: 共 {len(all_knowledge)} 个原始知识点")

    # 第二阶段：去重
    print(f"\n  [第二阶段] 去重处理...")
    deduplicated = deduplicate(all_knowledge)
    print(f"  基础去重: {len(all_knowledge)} → {len(deduplicated)} 条")

    if len(deduplicated) > 20:
        deduplicated = deduplicate_semantic(deduplicated)
        print(f"  语义去重后: {len(deduplicated)} 条")

    # 第三阶段：LLM 质量审核（按类型调整标准）
    if not skip_review and len(deduplicated) > 10:
        print(f"\n  [第三阶段] LLM 质量审核（{tcfg['name']}标准，重要性≥{tcfg['review_importance_threshold']}）...")
        reviewed = review_knowledge(deduplicated, book_title, detected_type)
    else:
        if skip_review:
            print(f"\n  [跳过第三阶段] --skip-review 模式")
        else:
            print(f"\n  [跳过第三阶段] 知识点数量较少（{len(deduplicated)} <= 10），无需审核")
        reviewed = deduplicated

    # 存储
    if dry_run:
        print(f"\n  [DRY RUN] 以下是将要保存的 {len(reviewed)} 条知识点：")
        for k in reviewed:
            print(f"    [{k.get('category', '?')}] {k['title']} (重要性: {k.get('importance', '?')})")
            print(f"      {k['content'][:100]}...")
        return 0

    print(f"\n  存储到知识库...")

    # 先清理旧数据，避免 REPLACE 导致 ID 变化和 ChromaDB 残留
    print(f"  清理旧数据: {book_title}")
    old_count = delete_knowledge_by_source(book_title)
    chroma_deleted = delete_chroma_by_filter("book", source=book_title)
    print(f"  已删除旧数据: SQLite {old_count} 条, ChromaDB {chroma_deleted} 条")

    saved = 0
    chroma_errors = 0
    for k in reviewed:
        try:
            knowledge_id = add_knowledge(
                category="book",
                title=f"[{book_title}] {k['title']}",
                content=k["content"],
                subcategory=k.get("category", "concept"),
                source=book_title,
                keywords=k.get("keywords", []),
                importance=k.get("importance", 7),
            )
            saved += 1

            # 同步写入 ChromaDB 向量库
            try:
                index_book_knowledge(
                    knowledge_id=knowledge_id,
                    title=f"[{book_title}] {k['title']}",
                    content=k["content"],
                    source=book_title,
                )
            except Exception as ce:
                chroma_errors += 1
                if chroma_errors <= 3:
                    print(f"    向量索引失败: {k['title'][:40]}... ({ce})")
        except Exception as e:
            print(f"  保存失败: {k['title']} - {e}")

    if chroma_errors > 0:
        print(f"  警告: {chroma_errors} 条知识点向量索引失败，SQLite 数据已保存")

    return saved


# ══════════════════════════════════════════════════════════════
# CLI 命令
# ══════════════════════════════════════════════════════════════

def _find_source_file(book_title: str) -> Path | None:
    """按优先级查找书籍源文件。

    查找顺序：
    1. /Users/xiaoyuer/projects/document/{书名}.md
    2. /Users/xiaoyuer/projects/document/{书名}.txt
    3. /Users/xiaoyuer/projects/document/{书名}.pdf
    4. data/books/{书名}.md
    5. data/books/{书名}.txt
    6. data/books/{书名}.pdf
    返回 None 表示未找到。
    """
    dirs = [DOCUMENT_DIR, BOOKS_DIR]
    exts = [".md", ".txt", ".pdf"]

    for d in dirs:
        for ext in exts:
            p = d / f"{book_title}{ext}"
            if p.exists():
                return p

    # 模糊匹配：document 目录下文件名包含书名的 PDF
    if DOCUMENT_DIR.exists():
        for p in DOCUMENT_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".pdf"}:
                # 书名是子串，或文件名是书名的子串
                if book_title in p.name or p.stem in book_title:
                    return p

    return None


def cmd_ocr(args):
    """只做 OCR：PDF → Markdown。"""
    print(f"{'=' * 60}")
    print(f"OCR 识别: {args.name}")
    print(f"输入文件: {args.input}")
    print(f"{'=' * 60}")

    pdf_to_markdown(args.input, args.name, args.start, args.end)

    print(f"\n{'=' * 60}")
    print(f"OCR 完成!")
    print(f"输出文件: {BOOKS_DIR / f'{args.name}.md'}")
    print(f"{'=' * 60}")


def cmd_distill(args):
    """只做蒸馏：自动查找或从指定文件蒸馏。"""
    print(f"{'=' * 60}")
    print(f"知识蒸馏: {args.name}")
    print(f"模型: {_get_model_name()}")
    print(f"{'=' * 60}")

    # 查找源文件
    input_path = None
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"错误: 指定文件不存在: {input_path}")
            sys.exit(1)
    else:
        input_path = _find_source_file(args.name)
        if input_path is None:
            print(f"错误: 未找到 '{args.name}' 的源文件")
            print(f"  查找路径: {DOCUMENT_DIR} → {BOOKS_DIR}")
            sys.exit(1)

    print(f"  找到源文件: {input_path}")

    # 读取文本
    if input_path.suffix.lower() == '.md' or input_path.suffix.lower() == '.txt':
        text = input_path.read_text(encoding="utf-8")
    elif input_path.suffix.lower() == '.pdf':
        # PDF 直接 OCR 读取（不入库 Markdown）
        text = pdf_to_markdown(str(input_path), args.name)
    else:
        print(f"错误: 不支持的文件格式 {input_path.suffix}")
        sys.exit(1)

    print(f"  文本长度: {len(text)} 字")

    # 蒸馏
    saved = distill_from_text(text, args.name,
                              dry_run=args.dry_run,
                              skip_review=args.skip_review,
                              book_type=args.type,
                              concurrency=args.concurrency)

    print(f"\n{'=' * 60}")
    print(f"蒸馏完成!")
    print(f"  书名: {args.name}")
    print(f"  成功保存: {saved} 条")
    print(f"{'=' * 60}")


def cmd_full(args):
    """完整流程：清理旧数据 → 自动查找源文件 → OCR(如需) → 蒸馏 → 入库。"""
    print(f"{'=' * 60}")
    print(f"完整蒸馏流程: {args.name}")
    print(f"模型: {_get_model_name()}")
    print(f"{'=' * 60}")

    # 第一步：清理旧数据（SQLite + ChromaDB）
    if not args.dry_run:
        print(f"\n[1/4] 清理旧数据...")
        old_count = delete_knowledge_by_source(args.name)
        chroma_count = delete_chroma_by_filter("book", source=args.name)
        print(f"  已清理: SQLite {old_count} 条, ChromaDB {chroma_count} 条")

    # 第二步：自动查找源文件（优先 document，其次 data/books）
    print(f"\n[2/4] 查找源文件...")
    input_path = None
    if args.input:
        # 用户显式指定了输入文件
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"  错误: 指定文件不存在: {input_path}")
            sys.exit(1)
    else:
        # 自动查找
        input_path = _find_source_file(args.name)
        if input_path is None:
            print(f"  错误: 未找到 '{args.name}' 的源文件")
            print(f"  查找路径: {DOCUMENT_DIR} → {BOOKS_DIR}")
            sys.exit(1)

    print(f"  找到源文件: {input_path}")

    # 第三步：读取/转换文本
    print(f"\n[3/4] 读取文本...")
    if input_path.suffix.lower() == '.pdf':
        # PDF 需要先 OCR 转成 Markdown（保存到 data/books 方便复用）
        text = pdf_to_markdown(str(input_path), args.name, args.start, args.end)
    elif input_path.suffix.lower() in ('.md', '.txt'):
        text = input_path.read_text(encoding="utf-8")
        print(f"  读取完成: {len(text)} 字")
    else:
        print(f"错误: 不支持的文件格式 {input_path.suffix}")
        sys.exit(1)

    # 第四步：蒸馏
    print(f"\n[4/4] 知识蒸馏...")
    saved = distill_from_text(text, args.name,
                              dry_run=args.dry_run,
                              skip_review=args.skip_review,
                              book_type=args.type,
                              concurrency=args.concurrency)

    print(f"\n{'=' * 60}")
    print(f"完整流程完成!")
    print(f"  书名: {args.name}")
    print(f"  源文件: {input_path}")
    print(f"  成功保存: {saved} 条知识点")
    print(f"  流程: 清理旧数据 → 查找源文件 → 读取 → 蒸馏 → 入库")
    print(f"{'=' * 60}")


def cmd_clean(args):
    """清理某书的旧数据（SQLite + ChromaDB + Markdown）。"""
    if not args.name:
        print("错误: 必须指定 --name 参数")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"清理数据: {args.name}")
    print(f"{'=' * 60}")

    count = delete_knowledge_by_source(args.name)
    print(f"\n  已删除 SQLite {count} 条知识点")

    # 同时删除 ChromaDB 向量
    chroma_count = delete_chroma_by_filter("book", source=args.name)
    print(f"  已删除 ChromaDB {chroma_count} 条向量")

    # 同时删除 Markdown 文件（data/books 目录）
    for ext in (".md", ".txt"):
        f = BOOKS_DIR / f"{args.name}{ext}"
        if f.exists():
            f.unlink()
            print(f"  已删除本地文件: {f}")

    print(f"\n清理完成!")


def cmd_list(args):
    """查看已蒸馏书籍列表。"""
    books = list_knowledge_books()

    if not books:
        print("暂无已蒸馏的书籍")
        return

    print(f"{'=' * 60}")
    print(f"已蒸馏书籍列表（共 {len(books)} 本）")
    print(f"{'=' * 60}")
    print(f"{'书名':<30} {'知识点数':>8} {'首次蒸馏':<20}")
    print(f"{'-' * 60}")

    for book in books:
        print(f"{book['source']:<30} {book['count']:>8} {book.get('first_created', 'N/A'):<20}")

    print(f"{'=' * 60}")


def cmd_reindex(args):
    """为已蒸馏的书籍重建 ChromaDB 向量索引。"""
    from db.knowledge import list_knowledge

    print(f"{'=' * 60}")
    print(f"重建向量索引: {args.name or '所有书籍'}")
    print(f"{'=' * 60}")

    if args.name:
        # 重建单本书
        books_to_reindex = [args.name]
    else:
        # 重建所有书
        books_info = list_knowledge_books()
        books_to_reindex = [b["source"] for b in books_info if b["source"]]
        print(f"发现 {len(books_to_reindex)} 本已蒸馏书籍")

    total_indexed = 0
    for book_name in books_to_reindex:
        print(f"\n  处理: {book_name}")
        items = list_knowledge(category="book", source=book_name, limit=10000)
        if not items:
            print(f"    无知识点，跳过")
            continue

        # 先清理该书的旧 ChromaDB 向量
        deleted = delete_chroma_by_filter("book", source=book_name)
        print(f"    清理旧向量: {deleted} 条")

        indexed = 0
        for item in items:
            try:
                index_book_knowledge(
                    knowledge_id=item["id"],
                    title=item["title"],
                    content=item["content"],
                    source=book_name,
                )
                indexed += 1
            except Exception as e:
                print(f"    索引失败 id={item['id']}: {e}")

        print(f"    重建完成: {indexed}/{len(items)} 条")
        total_indexed += indexed

    print(f"\n{'=' * 60}")
    print(f"重建完成! 共索引 {total_indexed} 条")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="统一书籍蒸馏脚本 — OCR + 蒸馏 + 质量审核 + 入库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 自动查找源文件并完整蒸馏（推荐）
  %(prog)s full --name "资产配置的艺术" --type allocation
  %(prog)s full --name "聪明的投资者" --type framework
  %(prog)s full --name "共同基金常识" --type allocation

  # 显式指定文件路径
  %(prog)s full --input /path/to/book.pdf --name "书名" --type framework
  %(prog)s distill --input /path/to/book.md --name "书名" --type practical

  # 并发提取 + 试运行（先测试质量）
  %(prog)s distill --name "债券投资实战" --type practical --concurrency 3 --dry-run

  # 只做 OCR
  %(prog)s ocr /path/to/book.pdf --name 书名

  # 清理旧数据（SQLite + ChromaDB）
  %(prog)s clean --name "资产配置的艺术"

  # 查看已蒸馏书籍 / 重建索引
  %(prog)s list
  %(prog)s reindex --name 书名
  %(prog)s reindex

源文件查找顺序：
  1. /Users/xiaoyuer/projects/document/{书名}.md / .txt / .pdf
  2. data/books/{书名}.md / .txt / .pdf

书籍类型说明：
  practical   - 实战操作型（彼得·林奇、手把手教你读财报、债券投资实战）
  framework   - 理念框架型（聪明的投资者、投资最重要的事、投资要义）
  allocation  - 资产配置型（资产配置的艺术、漫步华尔街、共同基金常识）
  cycle       - 市场周期型（周期）
  behavioral  - 行为心理型（思考，快与慢）
  commodity   - 商品科普型（黄金投资新时代）
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ocr 子命令
    p_ocr = subparsers.add_parser("ocr", help="PDF → Markdown（OCR 识别）")
    p_ocr.add_argument("input", help="PDF 文件路径")
    p_ocr.add_argument("--name", required=True, help="书名")
    p_ocr.add_argument("--start", type=int, help="起始页码（1-based）")
    p_ocr.add_argument("--end", type=int, help="结束页码（1-based）")

    # distill 子命令
    p_distill = subparsers.add_parser("distill", help="Markdown → 知识点（蒸馏），自动查找源文件")
    p_distill.add_argument("--input", help="显式指定 Markdown/文本/PDF 文件路径（不指定则自动查找）")
    p_distill.add_argument("--name", required=True, help="书名（用于自动查找和入库）")
    p_distill.add_argument("--type", choices=list(BOOK_TYPES.keys()), help=f"书籍类型（默认自动检测），可选: {', '.join(BOOK_TYPES.keys())}")
    p_distill.add_argument("--chunk-size", type=int, help="覆盖默认的 chunk 大小")
    p_distill.add_argument("--concurrency", type=int, default=1, help="并发提取数（默认1，避免API限流）")
    p_distill.add_argument("--dry-run", action="store_true", help="试运行，不写入数据库")
    p_distill.add_argument("--skip-review", action="store_true", help="跳过 LLM 质量审核")

    # full 子命令
    p_full = subparsers.add_parser("full", help="完整流程：清理旧数据 → 自动查找 → OCR(如需) → 蒸馏 → 入库")
    p_full.add_argument("--input", help="显式指定 PDF/文本/Markdown 文件路径（不指定则自动查找）")
    p_full.add_argument("--name", required=True, help="书名（用于自动查找和入库）")
    p_full.add_argument("--type", choices=list(BOOK_TYPES.keys()), help=f"书籍类型（默认自动检测），可选: {', '.join(BOOK_TYPES.keys())}")
    p_full.add_argument("--chunk-size", type=int, help="覆盖默认的 chunk 大小")
    p_full.add_argument("--concurrency", type=int, default=1, help="并发提取数（默认1）")
    p_full.add_argument("--start", type=int, help="起始页码（1-based）")
    p_full.add_argument("--end", type=int, help="结束页码（1-based）")
    p_full.add_argument("--dry-run", action="store_true", help="试运行，不写入数据库")
    p_full.add_argument("--skip-review", action="store_true", help="跳过 LLM 质量审核")

    # clean 子命令
    p_clean = subparsers.add_parser("clean", help="清理某书的旧数据")
    p_clean.add_argument("--name", required=True, help="书名")

    # list 子命令
    p_list = subparsers.add_parser("list", help="查看已蒸馏书籍列表")

    # reindex 子命令
    p_reindex = subparsers.add_parser("reindex", help="重建 ChromaDB 向量索引")
    p_reindex.add_argument("--name", help="书名（不指定则重建所有书籍）")

    args = parser.parse_args()

    # 初始化 RAG 索引（FTS5 + ChromaDB）
    from rag import init_fts, init_chroma
    init_fts()
    init_chroma()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "ocr":
        cmd_ocr(args)
    elif args.command == "distill":
        cmd_distill(args)
    elif args.command == "full":
        cmd_full(args)
    elif args.command == "clean":
        cmd_clean(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "reindex":
        cmd_reindex(args)


if __name__ == "__main__":
    main()
