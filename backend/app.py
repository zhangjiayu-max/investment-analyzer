"""投资分析助手 — FastAPI 后端"""

import asyncio
import json
import os
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent

from db import (
    init_db, create_task, update_task, get_task, list_tasks, delete_task,
    save_valuation, get_valuation_history, get_latest_valuation, list_valuation_indexes,
    sync_articles, list_articles, get_article, get_article_by_seq, get_article_by_url, create_article,
    update_article, create_analysis_record, update_analysis_record,
    get_analysis_records, get_analysis_record, get_valuation_by_image,
    list_all_analysis_records,
    list_agents, get_agent, create_agent as db_create_agent, update_agent, delete_agent,
    list_conversations, get_conversation, create_conversation, update_conversation, delete_conversation,
    get_messages, create_message,
    create_author_article, update_author_article, get_author_article_by_url,
    list_author_articles, get_author_article, delete_author_article, count_author_articles,
    create_linked_article, list_linked_articles, get_linked_article, delete_linked_article,
    update_linked_article_file,
)
from article_reader import fetch_article, download_images, extract_stock_codes
from market_data import get_stock_info, get_index_valuation
from valuation import analyze_stock, analyze_fund
from llm_service import analyze_article, analyze_article_stream, chat_about_investment, analyze_images_batch, chat_with_agent
from image_parser import ImageParser
from rag import init_fts, init_chroma, index_article, index_valuation, index_analysis_record, build_rag_context, build_rag_context_with_details, log_rag_search, index_author_article, index_skill_document, index_skill_extraction, index_to_chroma

app = FastAPI(title="投资分析助手", version="0.4.0")

# 后台分析任务进度跟踪
_analyze_progress: dict[int, dict] = {}
_analyze_cancel: set[int] = set()  # 被用户请求取消的 article_id
_analyze_tasks: dict[int, asyncio.Task] = {}  # 当前正在执行的图片分析 Task
_reanalyze_tasks: dict[int, asyncio.Task] = {}  # 单张图片重新分析 Task
_vision_semaphore = asyncio.Semaphore(3)  # 限制并发 vision API 调用数

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR = ROOT / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="article_images")
OUTPUT_DIR = ROOT / "output" / "tasks"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/tasks", StaticFiles(directory=str(OUTPUT_DIR)), name="task_images")
UPLOADS_DIR = ROOT / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def startup():
    init_db()
    init_fts()
    init_chroma()


# ── 请求模型 ──────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    url: str


class ChatRequest(BaseModel):
    question: str
    context: str = ""


# ── 任务 API ──────────────────────────────────────────

@app.post("/api/tasks")
async def create_task_api(req: CreateTaskRequest):
    """创建任务，后台异步执行抓取+分析。"""
    task_id = create_task(req.url)
    # 后台启动异步任务
    asyncio.create_task(_run_task(task_id, req.url))
    return {"task_id": task_id, "status": "pending"}


@app.get("/api/tasks")
async def list_tasks_api(limit: int = 50):
    """任务列表。"""
    return {"tasks": list_tasks(limit)}


@app.get("/api/tasks/{task_id}")
async def get_task_api(task_id: int):
    """任务详情。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.delete("/api/tasks/{task_id}")
async def delete_task_api(task_id: int):
    """删除任务。"""
    if not delete_task(task_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@app.get("/api/tasks/{task_id}/images")
async def get_task_images(task_id: int):
    """获取任务图片列表（本地路径 + URL）。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    local_images = task.get("local_images") or []
    images = []
    for path in local_images:
        filename = Path(path).name
        images.append({
            "local_path": path,
            "url": f"/static/tasks/{task_id}/images/{filename}",
        })
    return {"images": images}


# ── 任务执行逻辑 ──────────────────────────────────────

async def _run_task(task_id: int, url: str):
    """后台异步执行：抓取 → 下载图片 → 提取代码 → 分析。"""
    try:
        # 1. 抓取文章
        update_task(task_id, status="fetching")
        article = await fetch_article(url)

        update_task(task_id,
            title=article["title"],
            author=article["author"],
            publish_time=article["publish_time"],
            content_text=article["content_text"],
        )

        # 2. 下载图片
        images_dir = str(OUTPUT_DIR / str(task_id) / "images")
        local_images = await download_images(article["images"], images_dir)
        update_task(task_id, images_dir=images_dir, local_images=local_images)

        # 3. 提取代码 + 行情分析
        update_task(task_id, status="analyzing")
        codes = extract_stock_codes(article["content_text"])

        market_summary = {}
        for code in codes[:5]:
            try:
                info = get_stock_info(code)
                analysis = analyze_stock(code)
                market_summary[code] = {
                    "name": info.get("name", ""),
                    "pe": info.get("pe"),
                    "pb": info.get("pb"),
                    "recommendation": analysis.get("recommendation", ""),
                }
            except Exception as e:
                market_summary[code] = {"error": str(e)}

        update_task(task_id,
            codes_found=codes,
            market_data=market_summary,
        )

        # 4. LLM 分析
        llm_result = analyze_article(
            title=article["title"],
            content=article["content_text"],
            market_data=json.dumps(market_summary, ensure_ascii=False, indent=2) if market_summary else None,
        )

        update_task(task_id,
            llm_analysis=llm_result,
            status="done",
        )

    except Exception as e:
        update_task(task_id, status="error", error_msg=str(e))


# ── 兼容旧接口 ──────────────────────────────────────

@app.post("/api/analyze")
async def analyze_compat(req: CreateTaskRequest):
    """兼容旧接口，创建任务并等待完成返回结果。"""
    task_id = create_task(req.url)
    await _run_task(task_id, req.url)
    return get_task(task_id)


def _build_valuation_context(question: str) -> str:
    """从用户问题中提取关键词，检索匹配的指数估值数据，格式化为上下文文本。"""
    # 获取所有已入库的指数名称，用反向匹配（指数名→问题）解决中文分词难题
    all_indexes = list_valuation_indexes()
    # 去重得到唯一指数列表
    unique_indexes = {}
    for idx in all_indexes:
        code = idx["index_code"]
        if code not in unique_indexes:
            unique_indexes[code] = idx["index_name"]

    # 用指数名称（或其核心部分，如"白酒"匹配"中证白酒"）在问题中做子串匹配
    _prefixes = ("中证", "国证", "沪", "深", "恒生")
    _middles = ("全指", "综指", "50", "100", "200", "300", "500", "800", "1000")
    seen_codes = set()
    matched_indexes = []
    for code, name in unique_indexes.items():
        # 优先匹配完整名称
        if name in question and code not in seen_codes:
            seen_codes.add(code)
            matched_indexes.append({"index_code": code, "index_name": name})
            continue
        # 去掉常见前缀后匹配
        for prefix in _prefixes:
            core = name.replace(prefix, "", 1)
            if len(core) >= 2 and core in question and code not in seen_codes:
                seen_codes.add(code)
                matched_indexes.append({"index_code": code, "index_name": name})
                break
        else:
            # 去掉前缀+中间词后匹配（如"中证全指半导体"→"半导体"）
            for prefix in _prefixes:
                core = name.replace(prefix, "", 1)
                for mid in _middles:
                    core2 = core.replace(mid, "", 1)
                    if len(core2) >= 2 and core2 in question and code not in seen_codes:
                        seen_codes.add(code)
                        matched_indexes.append({"index_code": code, "index_name": name})
                        break
                if code in seen_codes:
                    break

    if not matched_indexes:
        return ""

    # 查询每个匹配指数的最新估值和近期趋势
    parts = []
    for idx in matched_indexes:
        code = idx["index_code"]
        name = idx["index_name"]

        index_metrics = [i for i in all_indexes if i["index_code"] == code]
        if not index_metrics:
            continue

        lines = [f"【{name}（{code}）】"]
        for metric in index_metrics:
            mt = metric["metric_type"]
            latest = get_latest_valuation(code, mt)
            if not latest:
                continue

            val = latest.get("current_value")
            pct = latest.get("percentile")
            danger = latest.get("danger_value")
            opp = latest.get("opportunity_value")
            zscore = latest.get("zscore")
            date = latest.get("snapshot_date", "")

            # 估值水平描述
            level = ""
            if pct is not None:
                if pct < 30:
                    level = "低估"
                elif pct < 70:
                    level = "合理"
                else:
                    level = "高估"

            line = f"  {mt}: 当前值={val}, 分位点={pct}%({level}), 危险值={danger}, 机会值={opp}"
            if zscore is not None:
                line += f", z-score={zscore}"
            if date:
                line += f" [{date}]"
            lines.append(line)

            # 近5日趋势
            history = get_valuation_history(code, 5, mt)
            if len(history) >= 2:
                trend_vals = [str(h["current_value"]) for h in reversed(history)]
                lines.append(f"    近{len(history)}日趋势: {'→'.join(trend_vals)}")

        if len(lines) > 1:
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """自由问答，自动关联估值数据。"""
    valuation_context = _build_valuation_context(req.question)
    answer = chat_about_investment(req.question, req.context, valuation_context)
    return {"answer": answer}


@app.post("/api/tasks/{task_id}/analyze-images")
async def analyze_task_images(task_id: int):
    """分析任务中的所有图片，提取结构化数据。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    local_images = task.get("local_images") or []
    if not local_images:
        raise HTTPException(400, "该任务没有图片")

    results = analyze_images_batch(local_images)
    return {"results": results}


@app.post("/api/analyze-image")
async def analyze_single_image(body: dict):
    """分析单张图片（传本地路径）。"""
    path = body.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(400, "图片路径无效")
    parser = ImageParser(model_type="mimo")
    result = parser.parse(path)
    return result


# ── 估值数据 API ──────────────────────────────────────


class ParseAndSaveRequest(BaseModel):
    path: str
    model_type: str = "mimo"  # "mimo" 或 "deepseek"
    source_url: str | None = None  # 来源文章链接
    snapshot_date: str | None = None  # 可选，默认今天


@app.post("/api/valuations/parse")
async def parse_and_save(req: ParseAndSaveRequest):
    """解析图片并存储估值数据。"""
    if not req.path or not Path(req.path).exists():
        raise HTTPException(400, "图片路径无效")

    parser = ImageParser(model_type=req.model_type)
    result = parser.parse(req.path)

    # 存入数据库
    valuation_id = save_valuation(result, source_image=req.path, source_url=req.source_url, snapshot_date=req.snapshot_date)
    result["id"] = valuation_id
    return result


@app.get("/api/valuations")
async def list_indexes():
    """列出所有有估值数据的指数。"""
    return {"indexes": list_valuation_indexes()}


@app.get("/api/valuations/{index_code}")
async def get_history(index_code: str, days: int = 30, metric_type: str = None):
    """查询某指数的估值历史。"""
    history = get_valuation_history(index_code, days, metric_type)
    latest = get_latest_valuation(index_code, metric_type)
    return {
        "index_code": index_code,
        "latest": latest,
        "history": history,
    }


@app.post("/api/rag/reindex")
async def reindex_rag():
    """重建 RAG 全文索引 + 向量索引。"""
    # 索引所有文章
    tasks = list_tasks(limit=500)
    article_count = 0
    for t in tasks:
        if t.get("content_text") or t.get("llm_analysis"):
            body = t.get("content_text", "") + "\n" + (t.get("llm_analysis", "") or "")
            index_article(t["id"], t.get("title", ""), body)
            index_to_chroma("article", str(t["id"]), t.get("title", ""), body[:5000])
            article_count += 1

    # 索引所有估值数据
    val_count = 0
    all_indexes = list_valuation_indexes()
    for idx in all_indexes:
        code = idx["index_code"]
        name = idx.get("index_name", code)
        latest = get_latest_valuation(code, idx.get("metric_type"))
        if latest:
            index_valuation(code, name, latest)
            val_count += 1

    # 索引作者文章
    author_count = 0
    author_articles = list_author_articles(status="done", limit=500)
    for a in author_articles:
        if a.get("content_text"):
            index_author_article(a["id"], a.get("title", ""), a["content_text"])
            index_to_chroma("author_article", str(a["id"]), a.get("title", ""), a["content_text"][:5000])
            author_count += 1

    # 索引 Skill 文档
    skill_doc_count = 0
    try:
        from db import _get_conn
        conn = _get_conn()
        skill_docs = conn.execute("SELECT * FROM skill_documents ORDER BY id DESC LIMIT 10").fetchall()
        for doc in skill_docs:
            index_skill_document(doc["id"], f"Skill文档-{doc['doc_type']}", doc["content"])
            index_to_chroma("skill", str(doc["id"]), f"Skill文档-{doc['doc_type']}", doc["content"][:8000])
            skill_doc_count += 1
        conn.close()
    except Exception:
        pass

    # 索引技能提取结果
    skill_count = 0
    try:
        from db import _get_conn
        conn = _get_conn()
        skills = conn.execute("""
            SELECT s.*, a.title FROM author_skills s
            JOIN author_articles a ON s.article_id = a.id
        """).fetchall()
        for s in skills:
            skill_data = {
                "cognitive_framework": json.loads(s["cognitive_framework"] or "[]"),
                "behavior_patterns": json.loads(s["behavior_patterns"] or "[]"),
                "knowledge_strengths": json.loads(s["knowledge_strengths"] or "[]"),
                "classic_quotes": json.loads(s["classic_quotes"] or "[]"),
            }
            index_skill_extraction(s["article_id"], s["title"], skill_data)
            skill_count += 1
        conn.close()
    except Exception:
        pass

    # 索引个人文档
    linked_count = 0
    try:
        linked_docs = list_linked_articles(limit=500)
        for doc in linked_docs:
            if not doc.get("file_path"):
                continue
            file_path = UPLOADS_DIR / doc["file_path"]
            if not file_path.exists():
                continue
            file_type = doc.get("file_type", "")
            content = ""
            try:
                if file_type in ("txt", "md"):
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                elif file_type == "pdf":
                    from PyPDF2 import PdfReader
                    reader = PdfReader(str(file_path))
                    pages = [p.extract_text() for p in reader.pages if p.extract_text()]
                    content = "\n\n".join(pages)
                elif file_type == "docx":
                    from docx import Document
                    d = Document(str(file_path))
                    content = "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
            except Exception:
                continue
            if content:
                index_to_chroma("linked_doc", str(doc["id"]), doc.get("title", ""), content)
                linked_count += 1
    except Exception:
        pass

    return {
        "ok": True,
        "articles_indexed": article_count,
        "valuations_indexed": val_count,
        "author_articles_indexed": author_count,
        "skill_docs_indexed": skill_doc_count,
        "skills_indexed": skill_count,
        "linked_docs_indexed": linked_count,
    }


# ══════════════════════════════════════════════════════
# Agent 对话系统 API
# ══════════════════════════════════════════════════════

class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str
    knowledge_scope: str = ""
    icon: str = "robot"


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    agent_id: int
    context_data: str = None


class SendMessageRequest(BaseModel):
    content: str


@app.get("/api/agents")
async def list_agents_api():
    """列出所有 Agent。"""
    return {"agents": list_agents()}


@app.post("/api/agents")
async def create_agent_api(req: CreateAgentRequest):
    """创建自定义 Agent。"""
    agent_id = db_create_agent(
        name=req.name, system_prompt=req.system_prompt,
        description=req.description, knowledge_scope=req.knowledge_scope, icon=req.icon,
    )
    return {"ok": True, "agent_id": agent_id}


@app.get("/api/conversations")
async def list_conversations_api():
    """对话列表。"""
    return {"conversations": list_conversations()}


@app.post("/api/conversations")
async def create_conversation_api(req: CreateConversationRequest):
    """创建对话。"""
    conv_id = create_conversation(title=req.title, agent_id=req.agent_id, context_data=req.context_data)
    return {"ok": True, "conversation_id": conv_id}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation_api(conv_id: int):
    """删除对话。"""
    delete_conversation(conv_id)
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages_api(conv_id: int, limit: int = 50):
    """获取对话消息历史。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    msgs = get_messages(conv_id, limit)
    return {"conversation": conv, "messages": msgs}


@app.post("/api/conversations/{conv_id}/messages")
async def send_message_api(conv_id: int, req: SendMessageRequest):
    """发送消息并获取 AI 回复。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    agent_prompt = agent["system_prompt"] if agent else "你是一位专业的投资分析师，请简洁明了地回答用户问题。"

    # 1. 存储用户消息
    create_message(conv_id, "user", req.content)

    # 2. RAG 检索（根据 agent 的 knowledge_scope 过滤）
    rag_types = []
    if agent and agent.get("knowledge_scope"):
        import json
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    rag_result = build_rag_context_with_details(req.content, content_types=rag_types if rag_types else None)
    rag_context = rag_result["context"]

    # 3. 获取对话历史
    history = get_messages(conv_id, limit=20)
    # 转为 LLM 消息格式（排除刚存的用户消息，chat_with_agent 会自行处理）
    msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

    # 4. 调用 LLM
    try:
        answer = chat_with_agent(agent_prompt, msg_list, rag_context)
    except Exception as e:
        answer = f"AI 回复失败: {str(e)}"

    # 5. 存储 AI 回复
    msg_id = create_message(conv_id, "assistant", answer)

    # 6. 记录 RAG 检索日志
    log_rag_search(
        conversation_id=conv_id,
        message_id=msg_id,
        query=req.content,
        keywords=rag_result.get("keywords", []),
        results=rag_result.get("results", []),
        content_types=rag_types if rag_types else None,
    )

    # 7. 自动更新对话标题（首条消息时）
    if len(history) <= 1 and conv.get("title") == "新对话":
        short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
        update_conversation(conv_id, title=short_title)

    # 构建来源摘要
    sources = []
    for r in rag_result.get("results", []):
        sources.append({
            "type": r.get("label", r.get("content_type", "")),
            "title": r.get("title", ""),
            "reference_id": r.get("reference_id", ""),
        })

    return {
        "answer": answer,
        "rag": {
            "keywords": rag_result.get("keywords", []),
            "sources": sources,
            "results_count": len(rag_result.get("results", [])),
        }
    }


@app.get("/api/conversations/{conv_id}/rag-logs")
async def get_rag_logs_api(conv_id: int, limit: int = 50):
    """获取对话的 RAG 检索日志。"""
    from db import _get_conn
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conv_id, limit)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


@app.get("/api/rag-logs")
async def get_all_rag_logs_api(limit: int = 100):
    """获取所有 RAG 检索日志。"""
    from db import _get_conn
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


@app.get("/api/rag-stats")
async def get_rag_stats_api(days: int = 7):
    """获取 RAG 检索统计。"""
    from db import _get_conn
    conn = _get_conn()

    # 总检索次数
    total = conn.execute("SELECT COUNT(*) FROM rag_logs").fetchone()[0]

    # 按天统计
    daily = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    # 热门关键词
    keywords_raw = conn.execute("""
        SELECT keywords FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT 100
    """, (f"-{days} days",)).fetchall()

    from collections import Counter
    keyword_counter = Counter()
    for row in keywords_raw:
        try:
            kws = json.loads(row[0] or "[]")
            for kw in kws:
                keyword_counter[kw] += 1
        except:
            pass
    top_keywords = [{"keyword": k, "count": c} for k, c in keyword_counter.most_common(20)]

    # 知识类型命中统计
    type_stats = conn.execute("""
        SELECT content_types, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY content_types
    """, (f"-{days} days",)).fetchall()

    type_counter = Counter()
    for row in type_stats:
        try:
            types = json.loads(row[0] or "[]")
            for t in types:
                type_counter[t] += 1
        except:
            pass
    type_distribution = [{"type": t, "count": c} for t, c in type_counter.most_common()]

    # 平均命中结果数
    avg_results = conn.execute("""
        SELECT AVG(results_count) FROM rag_logs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()[0] or 0

    conn.close()

    return {
        "total": total,
        "daily": [dict(r) for r in daily],
        "top_keywords": top_keywords,
        "type_distribution": type_distribution,
        "avg_results": round(avg_results, 1),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/proxy-image")
async def proxy_image(url: str):
    """代理图片请求，绕过微信防盗链。"""
    import httpx
    headers = {
        "Referer": "https://mp.weixin.qq.com/",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/537.36",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "图片获取失败")
        ct = resp.headers.get("content-type", "image/jpeg")
        return StreamingResponse(iter([resp.content]), media_type=ct)


# ── 文章管理 API ──────────────────────────────────────


@app.post("/api/articles/sync")
async def sync_articles_api():
    """从 articles.json 同步文章列表到 DB。"""
    articles_file = ROOT / "doc" / "articles.json"
    if not articles_file.exists():
        raise HTTPException(400, "articles.json 不存在")
    with open(articles_file) as f:
        articles = json.load(f)
    sync_articles(articles)
    count = len(list_articles())
    return {"ok": True, "total": count}


@app.get("/api/articles")
async def list_articles_api(status: str = None):
    """文章列表，可选按状态筛选。"""
    return {"articles": list_articles(status)}


@app.get("/api/gallery")
async def list_gallery_records(search: str = None, limit: int = 200):
    """图片浏览：列出所有分析记录，支持模糊搜索。"""
    return {"records": list_all_analysis_records(search, limit)}


# ══════════════════════════════════════════════════════
# 作者文章 API
# ══════════════════════════════════════════════════════

_crawl_semaphore = asyncio.Semaphore(3)  # 限制并发爬取数


@app.post("/api/author-articles/import")
async def import_author_articles():
    """从 Excel 导入作者文章（幂等，跳过已存在）。"""
    from import_articles import import_from_excel
    result = import_from_excel()
    return {"ok": True, **result}


@app.get("/api/author-articles")
async def list_author_articles_api(status: str = None, search: str = None, limit: int = 200):
    """作者文章列表。"""
    articles = list_author_articles(status=status, search=search, limit=limit)
    stats = count_author_articles()
    return {"articles": articles, "stats": stats}


@app.get("/api/author-articles/{article_id}")
async def get_author_article_api(article_id: int):
    """作者文章详情。"""
    article = get_author_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    return article


@app.delete("/api/author-articles/{article_id}")
async def delete_author_article_api(article_id: int):
    """删除作者文章。"""
    if not delete_author_article(article_id):
        raise HTTPException(404, "文章不存在")
    return {"ok": True}


class ExtractUrlRequest(BaseModel):
    url: str


@app.post("/api/author-articles/extract")
async def extract_article_from_url(body: ExtractUrlRequest):
    """从 URL 提取文章信息（通用网页，不依赖 Playwright）。"""
    import requests as req
    from bs4 import BeautifulSoup

    try:
        resp = req.get(body.url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"请求失败: {e}")

    soup = BeautifulSoup(resp.text, "lxml")

    # Extract title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)

    # Extract author
    author = ""
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"]
    og_site = soup.find("meta", property="og:site_name")
    if not author and og_site and og_site.get("content"):
        author = og_site["content"]

    # Extract publish time
    publish_time = ""
    for attr in [("property", "article:published_time"), ("name", "pubdate"),
                 ("name", "publish_time"), ("property", "og:article:published_time")]:
        tag = soup.find("meta", attrs=dict([attr]))
        if tag and tag.get("content"):
            publish_time = tag["content"][:19]
            break
    if not publish_time:
        time_tag = soup.find("time")
        if time_tag:
            publish_time = time_tag.get("datetime", "")[:19] or time_tag.get_text(strip=True)[:19]

    # Extract summary
    summary = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        summary = meta_desc["content"]
    elif soup.find("meta", property="og:description"):
        summary = soup.find("meta", property="og:description").get("content", "")

    # Extract content text (main body)
    content_text = ""
    for selector in ["article", '[role="main"]', "main", ".article-content",
                     ".post-content", ".entry-content", "#content", ".content"]:
        container = soup.select_one(selector)
        if container and len(container.get_text(strip=True)) > 100:
            content_text = container.get_text("\n", strip=True)
            break
    if not content_text:
        paragraphs = soup.find_all("p")
        content_text = "\n\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)

    if len(content_text) > 5000:
        content_text = content_text[:5000] + "..."

    return {
        "url": body.url,
        "title": title,
        "author": author,
        "publish_time": publish_time,
        "summary": summary[:500] if summary else "",
        "content_text": content_text,
    }


@app.post("/api/author-articles")
async def create_author_article_api(body: dict):
    """直接创建作者文章记录。"""
    article_id = create_author_article(
        url=body.get("url", ""),
        title=body.get("title", ""),
        publish_time=body.get("publish_time", ""),
        summary=body.get("summary", ""),
        article_type=body.get("article_type", ""),
    )
    if body.get("content_text"):
        update_author_article(article_id, content_text=body["content_text"])
    return {"id": article_id}


@app.post("/api/author-articles/crawl")
async def crawl_all_author_articles():
    """批量爬取所有 pending 状态的作者文章全文。"""
    pending = list_author_articles(status="pending", limit=500)
    if not pending:
        return {"ok": True, "message": "没有待爬取的文章", "total": 0}

    asyncio.create_task(_batch_crawl_author_articles(pending))
    return {"ok": True, "message": f"开始爬取 {len(pending)} 篇文章", "total": len(pending)}


@app.post("/api/author-articles/{article_id}/crawl")
async def crawl_single_author_article(article_id: int):
    """爬取单篇作者文章全文。"""
    article = get_author_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")

    update_author_article(article_id, status="crawling", error_msg="")
    asyncio.create_task(_crawl_one_author_article(article_id, article["url"]))
    return {"ok": True, "message": "开始爬取"}


def _clean_article_html(html: str) -> str:
    """清理微信文章 HTML，保留图文混排结构。"""
    import re
    # data-src → src（微信图片用 data-src 做懒加载）
    html = html.replace('data-src="', 'src="')
    # 移除 script/style 标签
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 noscript
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除微信特有的 data- 属性
    html = re.sub(r'\s+data-[a-z-]+="[^"]*"', '', html, flags=re.IGNORECASE)
    # 移除 onclick 等事件
    html = re.sub(r'\s+on[a-z]+="[^"]*"', '', html, flags=re.IGNORECASE)
    # 图片 src 走代理（微信图片）
    html = re.sub(
        r'src="(https?://mmbiz\.qpic\.cn/[^"]*)"',
        lambda m: f'src="/api/proxy-image?url={m.group(1)}"',
        html
    )
    # 移除空的 section/div（减少嵌套）
    html = re.sub(r'<(section|div)[^>]*>\s*</\1>', '', html, flags=re.IGNORECASE)
    return html.strip()


async def _batch_crawl_author_articles(articles: list):
    """后台批量爬取作者文章。"""
    for a in articles:
        update_author_article(a["id"], status="crawling")

    async def _crawl_with_limit(article_id, url):
        async with _crawl_semaphore:
            await _crawl_one_author_article(article_id, url)

    tasks = [_crawl_with_limit(a["id"], a["url"]) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _crawl_one_author_article(article_id: int, url: str):
    """爬取单篇作者文章并更新数据库。"""
    try:
        result = await fetch_article(url)
        content = result.get("content_text", "")
        raw_html = result.get("content_html", "")
        title = result.get("title", "")
        publish_time = result.get("publish_time", "")
        images = result.get("images", [])

        # 清理 HTML：data-src → src，过滤脚本/样式/空白标签
        clean_html = _clean_article_html(raw_html) if raw_html else ""

        update_author_article(article_id,
            content_text=content,
            content_html=clean_html,
            title=title or None,
            publish_time=publish_time or None,
            images=json.dumps(images, ensure_ascii=False) if images else None,
            status="done",
            error_msg="",
        )

        # 索引到 RAG
        if content:
            index_author_article(article_id, title, content)

    except Exception as e:
        update_author_article(article_id, status="error", error_msg=str(e)[:500])


@app.post("/api/articles/add")
async def add_article_api(req: CreateTaskRequest):
    """粘贴公众号链接，自动解析+下载+分析（全流程）。"""
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "链接不能为空")

    # 去重检查
    existing = get_article_by_url(url)
    if existing:
        return {"ok": False, "message": "文章已存在", "article_id": existing["id"], "status": existing["status"]}

    # 先插入占位记录
    article_id = create_article(url, title="解析中...")

    # 后台全流程：解析 → 下载 → 分析
    asyncio.create_task(_background_add_article(article_id, url))
    return {"ok": True, "message": "已提交，正在解析", "article_id": article_id}


@app.get("/api/articles/{article_id}")
async def get_article_api(article_id: int):
    """文章详情，含图片列表和分析记录。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    records = get_analysis_records(article_id)
    article["analysis_records"] = records
    return article


async def _background_add_article(article_id: int, url: str):
    """全流程后台任务：解析文章 → 下载图片 → 分析估值。"""
    try:
        # 1. 解析文章
        update_article(article_id, status="downloading")
        result = await fetch_article(url)
        title = result.get("title", "未知标题")
        publish_time = result.get("publish_time", "")
        images = result.get("images", [])

        if not images:
            update_article(article_id, status="error", error_msg="文章无图片", title=title, publish_time=publish_time)
            return

        # 更新文章信息
        update_article(article_id, title=title, publish_time=publish_time)

        # 2. 下载图片
        from datetime import datetime
        article_date = publish_time[:10] if publish_time else datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in title[:20] if c.isalnum() or c in " _-").strip() or "article"
        save_dir = IMAGES_DIR / article_date / safe_title
        save_dir.mkdir(parents=True, exist_ok=True)

        local_paths = await download_images(images, str(save_dir))

        manifest = {
            "url": url, "title": title, "article_date": article_date,
            "images": [{"index": i, "url": u, "local_path": str(lp)} for i, (u, lp) in enumerate(zip(images, local_paths))],
        }
        manifest_path = save_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        for i, (u, lp) in enumerate(zip(images, local_paths)):
            create_analysis_record(article_id, i, str(lp), u)

        images_dir_str = str(save_dir)
        try:
            images_dir_str = str(save_dir.relative_to(ROOT))
        except ValueError:
            pass

        update_article(article_id,
            status="downloaded", images_dir=images_dir_str,
            manifest_path=str(manifest_path), image_count=len(local_paths),
        )

        # 3. 自动分析
        update_article(article_id, status="analyzing")
        records = get_analysis_records(article_id)
        pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]
        parser = ImageParser(model_type="mimo")
        success = 0
        failed = 0

        for record in pending:
            rid = record["id"]
            img_path = record["image_path"]
            if not os.path.isabs(img_path):
                img_path = str(ROOT / img_path)
            if not os.path.exists(img_path):
                update_analysis_record(rid, status="error", error_msg="文件不存在")
                failed += 1
                continue

            try:
                async with _vision_semaphore:
                    parse_result = await asyncio.wait_for(
                        asyncio.to_thread(parser.parse, img_path), timeout=600,
                    )
                has_value = parse_result and parse_result.get("index_code") and parse_result.get("current_value") is not None
                if has_value:
                    save_valuation(parse_result, source_image=img_path, source_url=url, snapshot_date=article_date)
                    update_analysis_record(rid, status="success",
                        index_code=parse_result.get("index_code"), index_name=parse_result.get("index_name"),
                        metric_type=parse_result.get("metric_type"),
                        raw_response=json.dumps(parse_result, ensure_ascii=False))
                    success += 1
                else:
                    update_analysis_record(rid, status="error",
                        index_code=parse_result.get("index_code") if parse_result else None,
                        error_msg="AI 未能提取到数据",
                        raw_response=json.dumps(parse_result, ensure_ascii=False) if parse_result else "")
                    failed += 1
            except asyncio.TimeoutError:
                update_analysis_record(rid, status="timeout", error_msg="分析超时")
                failed += 1
            except Exception as e:
                update_analysis_record(rid, status="error", error_msg=str(e))
                failed += 1

        err_msg = f"{failed} 张失败" if failed > 0 else None
        update_article(article_id, status="analyzed", error_msg=err_msg)

    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))


@app.post("/api/articles/{article_id}/download")
async def download_article_images(article_id: int):
    """异步下载文章中的图片（后台执行）。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    if article["status"] == "downloading":
        return {"ok": False, "message": "正在下载中，请稍候"}

    update_article(article_id, status="downloading")
    asyncio.create_task(_background_download(article_id, article))
    return {"ok": True, "message": "下载已开始"}


async def _background_download(article_id: int, article: dict):
    """后台下载任务。"""
    try:
        result = await fetch_article(article["url"])
        images = result["images"]
        if not images:
            update_article(article_id, status="error", error_msg="无图片")
            return

        from datetime import datetime
        article_date = article.get("publish_time", "")[:10] or datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in (article["title"] or "")[:20] if c.isalnum() or c in " _-").strip() or "article"
        save_dir = IMAGES_DIR / article_date / safe_title
        save_dir.mkdir(parents=True, exist_ok=True)

        local_paths = await download_images(images, str(save_dir))

        manifest = {
            "url": article["url"],
            "title": article["title"],
            "article_date": article_date,
            "images": [
                {"index": i, "url": url, "local_path": str(lp)}
                for i, (url, lp) in enumerate(zip(images, local_paths))
            ],
        }
        manifest_path = save_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        for i, (url, lp) in enumerate(zip(images, local_paths)):
            create_analysis_record(article_id, i, str(lp), url)

        # 归一化 images_dir 为相对路径（相对于项目根）
        images_dir_str = str(save_dir)
        try:
            images_dir_str = str(save_dir.relative_to(ROOT))
        except ValueError:
            pass  # 不在 ROOT 下时保留原路径

        update_article(article_id,
            status="downloaded",
            images_dir=images_dir_str,
            manifest_path=str(manifest_path),
            image_count=len(local_paths),
        )
    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))


@app.post("/api/articles/{article_id}/analyze")
async def analyze_article_images(article_id: int):
    """异步分析文章中的所有图片（后台执行）。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")

    records = get_analysis_records(article_id)
    pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]
    if not pending:
        return {"ok": True, "message": "没有待分析的图片", "total": 0}
    if article["status"] == "analyzing":
        return {"ok": False, "message": "正在分析中，请稍候"}

    update_article(article_id, status="analyzing")
    _analyze_progress[article_id] = {"total": len(pending), "done": 0, "success": 0, "failed": 0, "current_record_id": None}
    asyncio.create_task(_background_analyze(article_id))
    return {"ok": True, "message": "分析已开始"}


@app.get("/api/articles/{article_id}/analyze-status")
async def get_analyze_status(article_id: int):
    """查询后台分析任务进度。"""
    article = get_article(article_id)
    progress = _analyze_progress.get(article_id, {})
    return {
        "status": article["status"] if article else "unknown",
        "progress": progress,
    }


@app.post("/api/articles/{article_id}/cancel-analyze")
async def cancel_analyze(article_id: int):
    """取消正在进行的分析任务。直接操作数据库，不依赖后台任务。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    if article["status"] != "analyzing":
        return {"ok": False, "message": "当前没有在分析中"}

    # 1. 设置取消标志 + 取消 asyncio Task
    _analyze_cancel.add(article_id)
    task = _analyze_tasks.get(article_id)
    if task and not task.done():
        task.cancel()

    # 2. 直接标记所有 pending/error 记录为 cancelled
    records = get_analysis_records(article_id)
    for r in records:
        if r["status"] in ("pending", "error"):
            update_analysis_record(r["id"], status="cancelled", error_msg="用户取消分析")

    # 3. 更新文章状态为 error
    update_article(article_id, status="error", error_msg="用户取消分析")

    # 4. 清理进度跟踪
    _analyze_progress.pop(article_id, None)

    return {"ok": True, "message": "已取消"}


async def _background_analyze(article_id: int):
    """后台分析任务。单张图片超时 10 分钟，取消时立即中断当前图片。"""
    IMAGE_TIMEOUT = 600  # 10 分钟
    current_task = None  # 当前图片的 asyncio Task，用于取消中断

    def _cancel_remaining(records_list, from_index):
        """标记从 from_index 开始的所有待处理记录为已取消。"""
        for r in records_list[from_index:]:
            if r["status"] in ("pending", "error"):
                update_analysis_record(r["id"], status="cancelled", error_msg="用户取消分析")

    try:
        article = get_article(article_id)
        records = get_analysis_records(article_id)
        pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]

        parser = ImageParser(model_type="mimo")
        success = 0
        failed = 0

        for idx, record in enumerate(pending):
            # 循环开头检查取消
            if article_id in _analyze_cancel:
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break

            rid = record["id"]
            img_path = record["image_path"]
            # 相对路径转绝对路径（相对于项目根目录）
            if not os.path.isabs(img_path):
                img_path = str(ROOT / img_path)

            # 记录当前正在分析的图片
            _analyze_progress[article_id]["current_record_id"] = rid

            if not os.path.exists(img_path):
                update_analysis_record(rid, status="error", error_msg="文件不存在")
                failed += 1
                _analyze_progress[article_id]["done"] += 1
                _analyze_progress[article_id]["failed"] = failed
                continue

            try:
                # 用 create_task 包装，这样可以被 task.cancel() 立即中断（信号量限制并发）
                async def _parse_with_semaphore():
                    async with _vision_semaphore:
                        return await asyncio.to_thread(parser.parse, img_path)

                current_task = asyncio.create_task(_parse_with_semaphore())
                _analyze_tasks[article_id] = current_task
                result = await asyncio.wait_for(current_task, timeout=IMAGE_TIMEOUT)

                has_value = (
                    result
                    and result.get("index_code")
                    and result.get("current_value") is not None
                )
                if has_value:
                    vid = save_valuation(
                        result,
                        source_image=img_path,
                        source_url=article["url"],
                        snapshot_date=article.get("publish_time", "")[:10] or None,
                    )
                    update_analysis_record(rid,
                        status="success",
                        index_code=result.get("index_code"),
                        index_name=result.get("index_name"),
                        metric_type=result.get("metric_type"),
                        raw_response=json.dumps(result, ensure_ascii=False),
                    )
                    success += 1
                else:
                    st = "success" if result.get("index_code") else "error"
                    update_analysis_record(rid,
                        status=st,
                        index_code=result.get("index_code"),
                        index_name=result.get("index_name"),
                        metric_type=result.get("metric_type"),
                        error_msg="AI 返回空值（无当前值）" if result.get("index_code") else "AI 未能提取到数据",
                        raw_response=json.dumps(result, ensure_ascii=False) if result else "",
                    )
                    failed += 1
            except asyncio.TimeoutError:
                update_analysis_record(rid, status="timeout", error_msg=f"分析超时（{IMAGE_TIMEOUT // 60}分钟）")
                failed += 1
            except asyncio.CancelledError:
                # 被用户取消
                update_analysis_record(rid, status="cancelled", error_msg="用户取消分析")
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx + 1)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break
            except Exception as e:
                update_analysis_record(rid, status="error", error_msg=str(e))
                failed += 1
            finally:
                current_task = None
                _analyze_tasks.pop(article_id, None)

            _analyze_progress[article_id]["done"] += 1
            _analyze_progress[article_id]["success"] = success
            _analyze_progress[article_id]["failed"] = failed
            _analyze_progress[article_id]["current_record_id"] = None

            # 每处理完一张后检查取消
            if article_id in _analyze_cancel:
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx + 1)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break

            await asyncio.sleep(0.3)

        # 仅当未被取消时才根据结果设置文章状态
        if article_id not in _analyze_cancel:
            all_records = get_analysis_records(article_id)
            total_success = len([r for r in all_records if r["status"] == "success"])
            if total_success > 0:
                update_article(article_id, status="analyzed")
            elif failed > 0:
                update_article(article_id, status="error", error_msg=f"{failed} 张图片分析失败")
    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))
    finally:
        _analyze_progress.pop(article_id, None)
        _analyze_tasks.pop(article_id, None)


@app.post("/api/records/{record_id}/reanalyze")
async def reanalyze_image(record_id: int):
    """重新分析单张图片（异步，后台执行，前端轮询查状态）。"""
    record = get_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")

    # 已有分析正在进行则直接返回
    task = _reanalyze_tasks.get(record_id)
    if task and not task.done():
        return {"ok": True, "message": "分析已在进行中"}

    # 重置记录状态为 analyzing
    update_analysis_record(record_id, status="analyzing", error_msg="")

    # 后台启动
    bt = asyncio.create_task(_background_reanalyze(record_id))
    _reanalyze_tasks[record_id] = bt
    return {"ok": True, "message": "分析已开始", "record_id": record_id}


@app.get("/api/records/{record_id}/reanalyze-status")
async def get_reanalyze_status(record_id: int):
    """查询单张图片重新分析的状态。"""
    record = get_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    task = _reanalyze_tasks.get(record_id)
    return {
        "record_id": record_id,
        "status": record["status"],
        "error_msg": record.get("error_msg"),
        "running": bool(task and not task.done()),
    }


async def _background_reanalyze(record_id: int):
    """后台重新分析单张图片（10分钟超时，不阻塞 event loop）。"""
    IMAGE_TIMEOUT = 600
    try:
        record = get_analysis_record(record_id)
        if not record:
            update_analysis_record(record_id, status="error", error_msg="记录不存在")
            return

        img_path = record["image_path"]
        if not os.path.isabs(img_path):
            img_path = str(ROOT / img_path)
        if not os.path.exists(img_path):
            update_analysis_record(record_id, status="error", error_msg="图片文件不存在")
            return

        article = get_article(record["article_id"])
        parser = ImageParser(model_type="mimo")

        # 异步执行 parser.parse，不阻塞 event loop（信号量限制并发）
        async with _vision_semaphore:
            result = await asyncio.wait_for(
                asyncio.to_thread(parser.parse, img_path),
                timeout=IMAGE_TIMEOUT,
            )

        has_value = (
            result
            and result.get("index_code")
            and result.get("current_value") is not None
        )
        if not has_value:
            err = "AI 返回空值（无当前值）" if result and result.get("index_code") else "AI 未能提取到数据"
            update_analysis_record(record_id,
                status="error",
                error_msg=err,
                raw_response=json.dumps(result, ensure_ascii=False) if result else "",
            )
            return

        vid = save_valuation(
            result,
            source_image=img_path,
            source_url=article["url"] if article else None,
            snapshot_date=article.get("publish_time", "")[:10] if article else None,
        )
        update_analysis_record(record_id,
            status="success",
            index_code=result.get("index_code"),
            index_name=result.get("index_name"),
            metric_type=result.get("metric_type"),
            raw_response=json.dumps(result, ensure_ascii=False),
        )
    except asyncio.TimeoutError:
        update_analysis_record(record_id, status="timeout", error_msg="分析超时（10分钟）")
    except Exception as e:
        update_analysis_record(record_id, status="error", error_msg=str(e))
    finally:
        _reanalyze_tasks.pop(record_id, None)


# ── 个人文档 API ──────────────────────────────────────────

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".doc"}

@app.get("/api/linked-articles")
async def list_linked_articles_api(limit: int = 200):
    return list_linked_articles(limit=limit)

@app.post("/api/linked-articles")
async def upload_document(file: UploadFile):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，仅支持 .txt / .md / .pdf / .docx")

    content = await file.read()
    file_size = len(content)
    title = Path(file.filename).stem

    article_id = create_linked_article(
        title=title, file_path="", file_size=file_size, file_type=ext.lstrip("."),
    )

    safe_name = f"{article_id}_{file.filename}"
    save_path = UPLOADS_DIR / safe_name
    save_path.write_bytes(content)

    update_linked_article_file(article_id, safe_name)

    # 异步 embedding（不阻塞响应）
    asyncio.create_task(_embed_linked_doc(article_id, ext.lstrip("."), content, title))

    return {"id": article_id}


async def _embed_linked_doc(article_id: int, file_type: str, raw_content: bytes, title: str):
    """后台任务：提取文档文本并 embedding。"""
    try:
        text = ""
        if file_type in ("txt", "md"):
            text = raw_content.decode("utf-8", errors="replace")
        elif file_type == "pdf":
            from PyPDF2 import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw_content))
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            text = "\n\n".join(pages)
        elif file_type == "docx":
            from docx import Document
            import io
            doc = Document(io.BytesIO(raw_content))
            text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if text.strip():
            index_to_chroma("linked_doc", str(article_id), title, text)
    except Exception:
        pass

@app.get("/api/linked-articles/{article_id}/download")
async def download_document(article_id: int):
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")
    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")
    return FileResponse(
        str(file_path),
        filename=article["title"] + "." + article.get("file_type", ""),
        media_type="application/octet-stream",
    )

@app.get("/api/linked-articles/{article_id}/content")
async def get_document_content(article_id: int):
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")
    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")

    file_type = article.get("file_type", "")
    content = ""

    try:
        if file_type in ("txt", "md"):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        elif file_type == "pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(str(file_path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            content = "\n\n".join(pages)
        elif file_type == "docx":
            from docx import Document
            doc = Document(str(file_path))
            content = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif file_type == "doc":
            content = "（.doc 格式暂不支持在线预览，请下载后查看）"
        else:
            content = "（不支持的文件格式）"
    except Exception as e:
        content = f"内容提取失败: {e}"

    return {"content": content, "file_type": file_type}


@app.delete("/api/linked-articles/{article_id}")
async def delete_linked_article_api(article_id: int):
    article = get_linked_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文档不存在")
    # Delete physical file
    if article.get("file_path"):
        file_path = UPLOADS_DIR / article["file_path"]
        if file_path.exists():
            file_path.unlink()
    delete_linked_article(article_id)
    return {"ok": True}


@app.post("/api/linked-articles/{article_id}/embed")
async def embed_document(article_id: int):
    """对单篇文档做 embedding 并存入向量库。"""
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")

    file_type = article.get("file_type", "")
    content = ""

    try:
        if file_type in ("txt", "md"):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        elif file_type == "pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(str(file_path))
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            content = "\n\n".join(pages)
        elif file_type == "docx":
            from docx import Document
            doc = Document(str(file_path))
            content = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif file_type == "doc":
            raise HTTPException(status_code=400, detail=".doc 格式暂不支持 embedding，请先转换为 .docx")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"内容提取失败: {e}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="文档内容为空，无法索引")

    chunks_count = index_to_chroma("linked_doc", str(article_id), article.get("title", ""), content)

    return {"ok": True, "chunks_indexed": chunks_count}


# ── 债市数据 API ──────────────────────────────────────────

@app.get("/api/bond/market-temperature")
async def get_bond_market_temperature():
    """抓取有知有行债市温度数据。"""
    import re, html as html_mod
    import requests as req

    try:
        resp = req.get(
            "https://youzhiyouxing.cn/data/macro",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据源请求失败: {e}")

    match = re.search(r'data-cbond-history="([^"]+)"', resp.text)
    if not match:
        raise HTTPException(status_code=502, detail="页面结构变化，未找到数据")

    raw = html_mod.unescape(match.group(1))
    # Find the end of the JSON array
    bracket_count = 0
    end_idx = 0
    for i, c in enumerate(raw):
        if c == '[':
            bracket_count += 1
        elif c == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end_idx = i + 1
                break

    try:
        data = json.loads(raw[:end_idx])
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"数据解析失败: {e}")

    # Extract current info from the last data point
    last = data[-1] if data else {}

    return {
        "history": data,
        "current": {
            "date": last.get("date"),
            "temperature": last.get("degree"),
            "rate": float(last["yield"]) if last.get("yield") else None,
        },
    }


# ── 前端页面 ──────────────────────────────────────────


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """Web 管理页面。"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>前端文件未找到</h1><p>请创建 static/index.html</p>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
