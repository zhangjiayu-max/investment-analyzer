from db.config import get_config_int, get_config_float
"""聚合技能特征 — 将 122 份提取结果合并为 1 份 Skill 文档"""

import json
from collections import Counter
from db import _get_conn
from config import get_llm_config
from services.llm_service import _call_llm

_api_key, _base_url, _model = get_llm_config()
MODEL = _model


def load_all_extractions() -> list[dict]:
    """加载所有技能提取结果"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT s.*, a.title
        FROM author_skills s
        JOIN author_articles a ON s.article_id = a.id
        ORDER BY s.id
    """).fetchall()
    conn.close()

    results = []
    for row in rows:
        try:
            results.append({
                "article_id": row["article_id"],
                "title": row["title"],
                "cognitive_framework": json.loads(row["cognitive_framework"] or "[]"),
                "behavior_patterns": json.loads(row["behavior_patterns"] or "[]"),
                "knowledge_strengths": json.loads(row["knowledge_strengths"] or "[]"),
                "knowledge_weaknesses": json.loads(row["knowledge_weaknesses"] or "[]"),
                "classic_quotes": json.loads(row["classic_quotes"] or "[]"),
            })
        except json.JSONDecodeError:
            continue
    return results


def aggregate_by_frequency(items: list[list[str]], top_n: int = 15) -> list[str]:
    """按频率聚合，取出现次数最多的 top_n 项"""
    counter = Counter()
    for item_list in items:
        for item in item_list:
            # 简单去重：去除相似表述
            counter[item] += 1
    return [item for item, count in counter.most_common(top_n)]


def llm_refine(category: str, items: list[str]) -> list[str]:
    """用 LLM 去重和精炼提取结果"""
    if not items:
        return []

    prompt = f"""请对以下 {category} 列表进行去重和精炼：

{json.dumps(items, ensure_ascii=False, indent=2)}

要求：
1. 合并相似的表述（如"短句为主"和"喜欢用短句"合并为一条）
2. 去除过于笼统的描述
3. 保留最具体、最有价值的 10-15 条
4. 输出 JSON 数组格式

只输出 JSON 数组，不要输出其他内容。"""

    try:
        response = _call_llm(
            caller="skill_aggregate",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=get_config_float('llm.temperature_tool', 0.2),
            max_tokens=get_config_int('llm.max_tokens_tool', 1000),
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    return json.loads(part)
        if text.startswith("["):
            return json.loads(text)
        # 尝试找到数组
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return items[:15]
    except Exception:
        return items[:15]


def generate_skill_document() -> str:
    """生成完整的 Skill 文档"""
    print("加载提取结果...")
    extractions = load_all_extractions()
    print(f"共 {len(extractions)} 篇文章的提取结果")

    if not extractions:
        return "暂无提取结果，请先运行 extract_skills.py"

    # 聚合各维度
    print("聚合认知框架...")
    frameworks = aggregate_by_frequency([e["cognitive_framework"] for e in extractions])
    frameworks = llm_refine("认知框架", frameworks)

    print("聚合作风模式...")
    patterns = aggregate_by_frequency([e["behavior_patterns"] for e in extractions])
    patterns = llm_refine("言行模式", patterns)

    print("聚合知识优势...")
    strengths = aggregate_by_frequency([e["knowledge_strengths"] for e in extractions])
    strengths = llm_refine("擅长领域", strengths)

    print("聚合知识劣势...")
    weaknesses = aggregate_by_frequency([e["knowledge_weaknesses"] for e in extractions])
    weaknesses = llm_refine("不擅长领域", weaknesses)

    print("聚合经典素材...")
    quotes = []
    for e in extractions:
        quotes.extend(e["classic_quotes"])
    # 去重
    quotes = list(set(quotes))
    quotes = llm_refine("经典观点和金句", quotes)

    # 生成文档
    doc = f"""# 研究员雷牛牛 - Skill Document

> 从 {len(extractions)} 篇公众号文章中蒸馏提取

---

## 认知框架

作者分析投资问题的核心方法论：

"""
    for i, fw in enumerate(frameworks, 1):
        doc += f"{i}. {fw}\n"

    doc += f"""
## 言行模式

作者的表达习惯和风格特征：

"""
    for pattern in patterns:
        doc += f"- {pattern}\n"

    doc += f"""
## 知识边界

### 擅长领域

"""
    for s in strengths:
        doc += f"- {s}\n"

    doc += f"""
### 不擅长领域

"""
    for w in weaknesses:
        doc += f"- {w}\n"

    doc += f"""
## 经典素材

作者高频引用的观点和金句：

"""
    for i, quote in enumerate(quotes, 1):
        doc += f"{i}. {quote}\n"

    doc += """
---

## 使用指南

### 作为 System Prompt

将本文档的核心内容注入 Agent 的 system_prompt 中：

```
你是研究员雷牛牛的AI助手。

## 思维方式
用他的分析框架思考问题：[认知框架内容]

## 表达风格
[言行模式内容]

## 知识边界
- 擅长：[擅长领域]
- 不擅长：[不擅长领域]

## 引用规则
引用作者观点时要注明来源：（来源：《文章标题》）
```

### 作为 RAG 增强

将本文档索引到 RAG 知识库，在对话时检索相关内容。
"""

    return doc


def save_skill_document(doc: str):
    """保存 Skill 文档到数据库和文件"""
    # 保存到数据库
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("INSERT INTO skill_documents (doc_type, content) VALUES (?, ?)", ("author_skill", doc))
    conn.commit()
    conn.close()

    # 保存到文件
    with open("skill_document.md", "w", encoding="utf-8") as f:
        f.write(doc)

    print(f"Skill 文档已保存到 skill_document.md")


if __name__ == "__main__":
    doc = generate_skill_document()
    save_skill_document(doc)
    print("\n=== Skill 文档预览（前 500 字）===")
    print(doc[:500])
