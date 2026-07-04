from db.config import get_config_int, get_config_float
"""批量蒸馏作者技能特征 — 从 122 篇文章中提取认知框架、言行模式、知识边界、经典素材"""

import json
import sys
import time
from config import get_llm_config
from db import _get_conn
from services.llm_service import _call_llm

_api_key, _base_url, _model = get_llm_config()
MODEL = _model

EXTRACT_PROMPT = """你是一位专业的投资分析师和文本分析专家。请分析以下公众号文章，提取作者的技能特征。

文章标题：{title}
文章内容：
{content}

请从以下 4 个维度提取特征，输出 JSON 格式：

{{
    "cognitive_framework": [
        "作者分析问题的方法论1",
        "作者分析问题的方法论2"
    ],
    "behavior_patterns": [
        "表达习惯1（如：短句为主、常用imo标记）",
        "表达习惯2"
    ],
    "knowledge_strengths": [
        "擅长的领域1",
        "擅长的领域2"
    ],
    "knowledge_weaknesses": [
        "不擅长或很少涉及的领域1"
    ],
    "classic_quotes": [
        "文章中的经典观点或金句1（保留原文）",
        "文章中的经典观点或金句2（保留原文）"
    ]
}}

提取要求：
1. cognitive_framework：提取作者分析投资问题的方法论、决策模型、思考框架
2. behavior_patterns：提取作者的表达习惯、口头禅、格式偏好（如短句、imo、《》引用等）
3. knowledge_strengths：提取作者擅长讨论的话题领域
4. knowledge_weaknesses：提取作者很少涉及或明显不擅长的领域
5. classic_quotes：提取文章中的经典观点、金句、高频引用（保留原文，不要改写）

每个维度提取 2-5 条，不要泛泛而谈，要具体、可操作。
只输出 JSON，不要输出其他内容。"""


def extract_skill_from_article(title: str, content: str) -> dict:
    """从单篇文章中提取技能特征"""
    if not content or len(content) < 100:
        return None

    # 截取前 6000 字，避免 token 过多
    truncated = content[:6000]

    try:
        response = _call_llm(
            caller="skill_extract",
            model=MODEL,
            messages=[
                {"role": "user", "content": EXTRACT_PROMPT.format(title=title, content=truncated)},
            ],
            temperature=get_config_float('llm.temperature_tool', 0.3),
            max_tokens=get_config_int('llm.max_tokens_tool', 1500),
        )
        text = response.choices[0].message.content.strip()

        # 尝试提取 JSON
        if "```" in text:
            # 提取 ``` 包裹的内容
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break

        # 尝试找到 JSON 对象
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  JSON 解析失败: {e}")
        return None
    except Exception as e:
        print(f"  LLM 调用失败: {e}")
        return None


def batch_extract(limit: int = None, skip_existing: bool = True):
    """批量提取所有文章的技能特征"""
    conn = _get_conn()

    # 查询需要提取的文章
    query = "SELECT id, title, content_text FROM author_articles WHERE status='done' AND content_text IS NOT NULL"
    if skip_existing:
        query += " AND (skill_extracted = 0 OR skill_extracted IS NULL)"
    if limit:
        query += f" LIMIT {limit}"

    # 确保 skill_extracted 字段存在
    try:
        conn.execute("ALTER TABLE author_articles ADD COLUMN skill_extracted INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass

    try:
        rows = conn.execute(query).fetchall()
    except Exception:
        # fallback: 不过滤 skill_extracted
        rows = conn.execute(query.replace("AND skill_extracted IS NULL", "")).fetchall()

    total = len(rows)
    print(f"待提取文章数: {total}")

    # 创建结果表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS author_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER UNIQUE,
            cognitive_framework TEXT,
            behavior_patterns TEXT,
            knowledge_strengths TEXT,
            knowledge_weaknesses TEXT,
            classic_quotes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()

    success = 0
    fail = 0

    for i, row in enumerate(rows):
        article_id = row["id"]
        title = row["title"] or "无标题"
        content = row["content_text"] or ""

        print(f"[{i+1}/{total}] 提取: {title[:30]}...")

        result = extract_skill_from_article(title, content)

        if result:
            # 保存到数据库
            conn.execute("""
                INSERT OR REPLACE INTO author_skills
                (article_id, cognitive_framework, behavior_patterns, knowledge_strengths, knowledge_weaknesses, classic_quotes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                article_id,
                json.dumps(result.get("cognitive_framework", []), ensure_ascii=False),
                json.dumps(result.get("behavior_patterns", []), ensure_ascii=False),
                json.dumps(result.get("knowledge_strengths", []), ensure_ascii=False),
                json.dumps(result.get("knowledge_weaknesses", []), ensure_ascii=False),
                json.dumps(result.get("classic_quotes", []), ensure_ascii=False),
            ))
            conn.execute("UPDATE author_articles SET skill_extracted=1 WHERE id=?", (article_id,))
            conn.commit()
            success += 1
            print(f"  成功")
        else:
            fail += 1
            print(f"  失败")

        # 避免 API 限流
        time.sleep(1)

    conn.close()
    print(f"\n完成: 成功 {success}, 失败 {fail}, 共 {total}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    batch_extract(limit=limit)
