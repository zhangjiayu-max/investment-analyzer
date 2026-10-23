"""知识库治理：领域划分、流派标注、时序标注、原子类型修复。

## 为什么需要这一层

知识库当前的问题是「存了大模型已有的东西，而大模型没有的东西没存进来」，
叠加三个结构性缺陷：

1. **无领域边界** — 579 条个人笔记里 578 条是 AI 工程书籍笔记，
   与投资检索共享同一向量空间，按语义相似度竞争召回。
2. **无流派标识** — 价值投资与技术交易共 2676 条同库共存，
   检索按相似度投票，同一个问题会吐出结论相反的两派观点，且不自称立场。
3. **无时序语义** — `as_of_date` 填写率 0.11%，1896 年的统计与 2026 年的规律
   被同等对待。

本模块用**确定性规则**（不用 LLM 猜）补齐这三层元数据，原则：

- 书名 → 流派：手工映射表，可审计、可复现、可 review
- 来源路径 → 领域：路径前缀匹配，不做语义推断
- 原子类型 → 时序类型：数据类型与时点绑定，原理类默认长期有效

## 不物理删除

非本域内容只标注 `domain`，靠检索层过滤，记录本身保留。
理由：Obsidian 是用户的原始资料，知识库是它的投影，
在投影层删数据会让人误以为源文件也没了。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 流派（school）—— 按书手工映射，粒度=书，不按原子猜
# ══════════════════════════════════════════════════════════════
#
# 之所以按「书」而不是按「原子」打流派标签：
# 一本书的方法论是自洽的，抽出的原子条目共享同一套前提假设。
# 按原子做语义分类会把「止损执行的黄金法则」判成风险控制（价值派也用），
# 从而丢掉它真正的方法论出处。按书打标签虽然粗，但永远不会自相矛盾。

SCHOOL_LABELS = {
    "value": "价值",
    "technical": "技术交易",
    "quant": "量化组合",
    "macro": "宏观周期",
    "behavioral": "行为金融",
    "history": "历史实证",
    "asset": "品种工具",
    "general": "通用",
}

SCHOOL_BY_BOOK = {
    # ── 价值投资 ──
    "雪球名家带你价值投资": "value",
    "巴菲特致合伙人致股东的信": "value",
    "彼得·林奇的成功投资": "value",
    "聪明的投资者": "value",
    "投资最重要的事": "value",
    "投资中最简单的事": "value",
    "投资要义": "value",
    "股市进阶之道": "value",
    "手把手教你读财报": "value",
    "港股滚雪球": "value",
    # ── 技术分析 / 交易系统 ──
    "专业投机理论": "technical",
    "以交易为生": "technical",
    "笑傲股市": "technical",
    "日本蜡烛图技术新解": "technical",
    "通向财务自由之路": "technical",  # Van Tharp：头寸规模+系统化交易
    # ── 量化 / 组合理论 ──
    "主动投资组合管理": "quant",
    "风险管理与金融机构": "quant",
    "漫步华尔街": "quant",  # 有效市场假说 + 指数化
    "全球资产配置": "quant",
    "资产配置的艺术": "quant",
    # ── 宏观 / 周期 ──
    "经济学原理：宏观经济学分册": "macro",
    "周期": "macro",
    "周期、估值与人性": "macro",
    "房地产周期": "macro",
    "金融炼金术": "macro",  # 索罗斯反身性
    # ── 行为金融 ──
    "思考，快与慢": "behavioral",
    # ── 历史实证 ──
    "股市长线法宝": "history",
    "黄金简史": "history",
    "中国生猪产业波动规律及调控对策研究": "history",
    "中国生猪市场价格波动研究": "history",
    # ── 品种 / 工具专题 ──
    "可转债投资魔法书": "asset",
    "债券投资实战": "asset",
    "黄金投资新时代": "asset",
    "共同基金常识": "asset",
    "解读基金": "asset",
    "指数基金投资指南": "asset",
    "定投十年财务自由": "asset",
}

# ══════════════════════════════════════════════════════════════
# 领域（domain）
# ══════════════════════════════════════════════════════════════

DOMAIN_INVESTMENT = "investment"
DOMAIN_AI_ENGINEERING = "ai_engineering"
DOMAIN_OTHER = "other"

# 来源路径/书名标记 → 非投资领域。用路径前缀匹配，不做语义判断。
_NON_INVESTMENT_SOURCE_MARKERS = (
    "AI工程大模型应用开发实战",
    "LangChain核心技术与LLM项目实践",
    "AI Agent开发与应用",
    "未来之地",
)

# 内容标记（仅在来源无法判定时兜底使用）
_AI_CONTENT_MARKERS = (
    "LangChain", "LLM", "PromptTemplate", "prompt", "RAG", "Embedding",
    "向量数据库", "微调", "token", "Token", "Agent开发", "LangSmith",
    "FAISS", "FAISS", "ChromaDB", "LoRA", "OpenAI API", "回调处理程序",
)

_FINANCE_CONTENT_MARKERS = (
    "估值", "市盈率", "股息", "仓位", "回撤", "净值", "基金", "债券", "股票",
    "指数", "年化", "收益率", "护城河", "安全边际", "利率", "通胀", "资产配置",
)


def infer_domain(category: str, source: str = "", title: str = "", content: str = "") -> str:
    """判定知识条目的领域。确定性规则，按优先级短路。

    优先级：
    1. category='book' → 投资（库内书籍全部为投资书目）
    2. 来源路径命中非投资书目标记 → ai_engineering
    3. 内容标记对比：AI 标记数 > 金融标记数 → ai_engineering
    4. 其余 → investment
    """
    source = source or ""
    text = f"{title or ''} {content or ''}"

    for marker in _NON_INVESTMENT_SOURCE_MARKERS:
        if marker in source:
            return DOMAIN_AI_ENGINEERING

    if category == "book":
        # 书库内均为投资书目；仅当内容明显是 AI 工程且来源无书名时才降级
        return DOMAIN_INVESTMENT

    if category in ("fund", "concept", "strategy"):
        return DOMAIN_INVESTMENT

    ai_hits = sum(1 for m in _AI_CONTENT_MARKERS if m in text)
    fin_hits = sum(1 for m in _FINANCE_CONTENT_MARKERS if m in text)
    if ai_hits > fin_hits and ai_hits >= 2:
        return DOMAIN_AI_ENGINEERING
    if fin_hits == 0 and ai_hits == 0:
        return DOMAIN_OTHER
    return DOMAIN_INVESTMENT


# ══════════════════════════════════════════════════════════════
# 时序（temporal_type）
# ══════════════════════════════════════════════════════════════

TEMPORAL_TIMELESS = "timeless"          # 方法论/原理，不随时间失效
TEMPORAL_DATED = "dated"                # 绑定具体时点的数据/事件
TEMPORAL_REGIME_DEPENDENT = "regime_dependent"  # 政策/制度，随环境变化
TEMPORAL_UNDATED = "undated"            # 声称有数据但无时点 —— 需补

_YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20[0-4]\d)(?!\d)")

# 时效敏感的原子类型：这些类型的内容一旦带数字，就必须标注数据时点
_DATED_ATOM_TYPES = {"data"}

# 实证声明标记：只有出现这些词，才说明该条目在引用某段时期的数据，
# 才需要 as_of_date。否则「市盈率的倒数是盈利收益率，例如市盈率 8 倍时
# 盈利收益率 12.5%」这类定义/算例会被误判成「有时效的数据」。
# 案例：v1 判据只看 atom_type=='data'，把 1160 条概念定义打成了 undated。
_EMPIRICAL_CLAIM_MARKERS = (
    "数据显示", "研究表明", "研究显示", "统计", "截至", "历史数据",
    "年间", "年以来", "年均", "回溯", "实证", "样本", "调查显示",
    "过去", "近年", "复盘",
)


def extract_years(text: str) -> list[int]:
    """提取文本中的四位年份（1850-2049）。"""
    if not text:
        return []
    years = []
    for m in _YEAR_RE.finditer(text):
        try:
            years.append(int(m.group(1)))
        except ValueError:
            continue
    return years


def infer_temporal(atom_type: str, content: str) -> tuple[str, str]:
    """返回 (temporal_type, as_of_date)。

    取「众数年份」而非「最早/最晚年份」：一段文字里被反复引用的年份通常是
    这段内容的数据锚点，偶然提到的年份（如"2020 年疫情"作类比）只出现一次。

    平票时取较早年份 —— 宁可把数据当成更旧的，也不要当更新的。
    """
    atom_type = atom_type or ""
    text = content or ""
    years = extract_years(text)

    if atom_type in _DATED_ATOM_TYPES:
        if not years:
            # 没有年份：只有确实在引用实证数据时才标记「缺时点」，
            # 纯定义/算例按长期有效处理。
            if any(m in text for m in _EMPIRICAL_CLAIM_MARKERS):
                return TEMPORAL_UNDATED, ""
            return TEMPORAL_TIMELESS, ""
        counter = Counter(years)
        top = max(counter.values())
        candidates = sorted(y for y, c in counter.items() if c == top)
        return TEMPORAL_DATED, str(candidates[0])

    if atom_type == "policy":
        if years:
            counter = Counter(years)
            top = max(counter.values())
            candidates = sorted(y for y, c in counter.items() if c == top)
            return TEMPORAL_REGIME_DEPENDENT, str(candidates[0])
        return TEMPORAL_REGIME_DEPENDENT, ""

    if atom_type in ("rule", "principle", "checklist", "concept", "strategy",
                     "insight", "fact", ""):
        # 原理类默认长期有效；但若正文含"当前/如今/目前+年份"，说明结论有时点依赖
        if years and any(k in (content or "") for k in ("当前", "目前", "如今", "现阶段")):
            counter = Counter(years)
            top = max(counter.values())
            candidates = sorted(y for y, c in counter.items() if c == top)
            return TEMPORAL_REGIME_DEPENDENT, str(candidates[0])
        return TEMPORAL_TIMELESS, ""

    return TEMPORAL_TIMELESS, ""


# ══════════════════════════════════════════════════════════════
# 原子类型修复
# ══════════════════════════════════════════════════════════════

# 合法原子类型（与 services/rag 的加权逻辑对齐）
VALID_ATOM_TYPES = {
    "rule", "principle", "checklist", "concept", "data",
    "insight", "policy", "strategy", "fact", "user_lesson",
}

# book/note 分类下不允许出现的原子类型：书摘不可能是"用户教训"
_FORBIDDEN_ATOM_TYPES = {
    "book": {"user_lesson"},
    "note": {"user_lesson"},
}

# 标题里的定义性标记：这类条目是概念解释，不是实证数据
_DEFINITION_TITLE_MARKERS = ("的概念", "的定义", "的含义", "是什么", "释义")


def repair_atom_type(category: str, atom_type: str, content: str,
                     title: str = "") -> tuple[str, str]:
    """修正明显错误的原子类型。返回 (新类型, 原因)。

    修复两类历史误标：

    1. `atom_type='user_lesson'` 只允许出现在 category='user_lesson' 下。
       历史上有 414 条书摘/笔记被误标成 user_lesson，导致：
         - 检索时被当作「用户亲身教训」给予权威加成（+0.12）
         - `get_lessons_for_target()` 按 category='user_lesson' 查询，
           而这类记录 category 是 book/note，永远查不到 —— 教训子系统整体空转
    2. 标题明显是定义（「XX 的概念」「XX 的含义」）却被标成 data。
       这类条目没有数据时点，被当成实证数据会让「有时效」的判断全部失真。
    """
    forbidden = _FORBIDDEN_ATOM_TYPES.get(category, set())
    if atom_type in forbidden:
        text = content or ""
        numeric = len(re.findall(r"\d+(?:\.\d+)?\s*%", text)) + len(_YEAR_RE.findall(text))
        if numeric >= 2:
            return "data", f"{category} 分类不应有 user_lesson，含 {numeric} 处量化事实 → data"
        return "insight", f"{category} 分类不应有 user_lesson → insight"

    if atom_type == "data" and title:
        if any(m in title for m in _DEFINITION_TITLE_MARKERS):
            # 定义类算例虽然含百分比，但不对应任何时点的观测
            return "concept", "标题为定义性表述，data → concept"

    return atom_type, ""


# ══════════════════════════════════════════════════════════════
# Schema 迁移
# ══════════════════════════════════════════════════════════════

GOVERNANCE_COLUMNS = (
    ("domain", "TEXT DEFAULT ''"),
    ("school", "TEXT DEFAULT ''"),
    ("temporal_type", "TEXT DEFAULT ''"),
    ("retrieval_count", "INTEGER DEFAULT 0"),
    ("last_retrieved_at", "TEXT DEFAULT ''"),
)


def migrate_schema(conn: sqlite3.Connection) -> list[str]:
    """幂等补齐治理字段，返回本次新增的列名。"""
    from db._utils import _add_column_if_not_exists

    existing = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_base)")}
    added = []
    for col, typ in GOVERNANCE_COLUMNS:
        if col not in existing:
            _add_column_if_not_exists(conn, "knowledge_base", col, typ)
            added.append(col)
    conn.commit()
    if added:
        logger.info(f"knowledge_base 新增治理字段: {added}")
    # 治理查询索引
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_domain ON knowledge_base(domain)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_school ON knowledge_base(school)")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return added


# ══════════════════════════════════════════════════════════════
# 治理动作
# ══════════════════════════════════════════════════════════════

def classify_entries(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """标注 domain / school，并修复不可能的 atom_type。"""
    rows = conn.execute("""
        SELECT id, category, source, title, content, atom_type
        FROM knowledge_base
    """).fetchall()

    domain_changes: list[tuple[str, int]] = []
    school_changes: list[tuple[str, int]] = []
    atom_changes: list[tuple[str, str, int]] = []

    domain_counter: Counter = Counter()
    school_counter: Counter = Counter()
    unknown_books: Counter = Counter()

    for r in rows:
        kid = r["id"]
        category = r["category"]
        source = r["source"] or ""
        title = r["title"] or ""
        content = r["content"] or ""
        atom_type = r["atom_type"] or ""

        # ── domain ──
        domain = infer_domain(category, source, title, content)
        domain_counter[domain] += 1
        domain_changes.append((domain, kid))

        # ── school ──
        school = ""
        if category == "book":
            school = SCHOOL_BY_BOOK.get(source, "")
            if not school:
                unknown_books[source] += 1
        elif domain == DOMAIN_INVESTMENT and category in ("concept", "strategy", "fund"):
            school = "general"
        school_counter[school or "(空)"] += 1
        school_changes.append((school, kid))

        # ── atom_type ──
        new_type, reason = repair_atom_type(category, atom_type, content, title)
        if new_type != atom_type:
            atom_changes.append((new_type, reason, kid))

    result = {
        "total": len(rows),
        "domain": dict(domain_counter),
        "school": dict(school_counter),
        "atom_type_repaired": len(atom_changes),
        "unknown_books": dict(unknown_books),
        "dry_run": dry_run,
    }

    if not dry_run:
        with conn:
            conn.executemany(
                "UPDATE knowledge_base SET domain=? WHERE id=?", domain_changes)
            conn.executemany(
                "UPDATE knowledge_base SET school=? WHERE id=?", school_changes)
            conn.executemany(
                "UPDATE knowledge_base SET atom_type=? WHERE id=?",
                [(t, kid) for t, _, kid in atom_changes])
    return result


def annotate_temporal(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """补 temporal_type / as_of_date。已有人工填写值的不覆盖。"""
    rows = conn.execute("""
        SELECT id, atom_type, content, as_of_date, temporal_type
        FROM knowledge_base
        WHERE COALESCE(temporal_type, '') = ''
           OR (COALESCE(as_of_date, '') = '' AND atom_type = 'data')
    """).fetchall()

    updates: list[tuple[str, str, int]] = []
    dist: Counter = Counter()
    undated_ids: list[int] = []

    for r in rows:
        kid = r["id"]
        atom_type = r["atom_type"] or ""
        content = r["content"] or ""
        existing_as_of = (r["as_of_date"] or "").strip()

        ttype, as_of = infer_temporal(atom_type, content)
        if existing_as_of:          # 人工已填，尊重原值
            as_of = existing_as_of
        dist[ttype] += 1
        if ttype == TEMPORAL_UNDATED:
            undated_ids.append(kid)
        updates.append((ttype, as_of, kid))

    result = {
        "scanned": len(rows),
        "temporal_type": dict(dist),
        "undated_data_atoms": len(undated_ids),
        "undated_sample_ids": undated_ids[:20],
        "dry_run": dry_run,
    }
    if not dry_run and updates:
        with conn:
            conn.executemany(
                "UPDATE knowledge_base SET temporal_type=?, as_of_date=? WHERE id=?",
                updates)
    return result


def refresh_usefulness(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """重算 usefulness_score（0-10 分）= 质量先验 + 人工反馈修正。

    先验只看**证据质量**，不看观点是否讨喜：

        5  基线
      +2  data 且有明确时点        （可复核的事实）
      +1  rule / checklist          （可执行）
      +1  带 counterpoints          （作者自己承认反例）
      +3  category='user_lesson'    （唯一「只有本系统有」的证据）
      -1  data 但无时点             （有时效声称却无法定位）
      -1  正文 < 80 字              （碎片，缺乏推理链）
      -2  明确属非投资领域           （对本系统检索是噪音）
      -2  temporal_type=undated 的 data（最危险：看起来是数据，查不到出处）

    为什么是 0-10 而不是以 0 为中心的加减分：
    绝大多数条目不该有负分。一个「多数条目为负」的分数在排序里只能做减法，
    无法区分「平庸」与「好」，等于没做。基线 5 让分布落在中间，
    高低两端才有信息量。

    注意：domain 为空（尚未治理）时不扣分 —— 未标注不等于非本域。
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER DEFAULT 0,
            content_type TEXT DEFAULT '',
            query TEXT DEFAULT '',
            rating INTEGER NOT NULL,
            reasons TEXT DEFAULT '[]',
            user_id TEXT DEFAULT 'default',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    fb = {}
    for r in conn.execute(
        "SELECT knowledge_id, SUM(rating) s FROM rag_feedback "
        "WHERE knowledge_id IS NOT NULL AND knowledge_id > 0 GROUP BY knowledge_id"
    ):
        fb[int(r[0])] = int(r[1] or 0)

    rows = conn.execute("""
        SELECT id, category, atom_type, content, as_of_date, temporal_type,
               domain, limitations, counterpoints, usefulness_score
        FROM knowledge_base
    """).fetchall()

    updates: list[tuple[int, int]] = []
    dist: Counter = Counter()
    for r in rows:
        kid = r["id"]
        score = 5
        atom_type = r["atom_type"] or ""
        temporal = r["temporal_type"] or ""
        as_of = (r["as_of_date"] or "").strip()
        content = r["content"] or ""
        domain = r["domain"] or ""

        if atom_type == "data" and as_of:
            score += 2
        if atom_type in ("rule", "checklist"):
            score += 1
        try:
            has_counter = bool(json.loads(r["counterpoints"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            has_counter = False
        if has_counter:
            score += 1
        if atom_type == "data" and not as_of:
            score -= 1
        if len(content) < 80:
            score -= 1
        if domain and domain != DOMAIN_INVESTMENT:
            score -= 2
        if temporal == TEMPORAL_UNDATED:
            score -= 2
        if (r["category"] or "") == "user_lesson":
            score += 3

        score += max(-5, min(5, fb.get(kid, 0)))
        score = max(0, min(10, score))

        dist[str(score)] += 1
        if score != (r["usefulness_score"] or 0):
            updates.append((score, kid))

    result = {
        "scanned": len(rows),
        "changed": len(updates),
        "score_distribution": dict(sorted(dist.items(), key=lambda kv: int(kv[0]))),
        "feedback_signals": len(fb),
        "dry_run": dry_run,
    }
    if not dry_run and updates:
        with conn:
            conn.executemany(
                "UPDATE knowledge_base SET usefulness_score=? WHERE id=?", updates)
    return result


def sync_retrieval_stats(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """从 rag_logs 统计每条知识被检索的次数与最近时间。

    用途不是排序（被检索多 ≠ 有用，会造成富者愈富），
    而是**观测**：找出从未被任何查询召回的条目 —— 这些是「入库即沉默」的存量，
    是压缩/淘汰的候选，也是检索策略失效的直接证据。
    """
    counts: Counter = Counter()
    last: dict[int, str] = {}
    rows = conn.execute(
        "SELECT results, created_at FROM rag_logs "
        "WHERE results IS NOT NULL AND results != ''"
    ).fetchall()
    for r in rows:
        try:
            items = json.loads(r[0])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("content_type") not in ("book", "note", "concept", "strategy"):
                continue
            ref = str(it.get("reference_id") or "")
            if not ref.isdigit():
                continue
            kid = int(ref)
            counts[kid] += 1
            ts = r[1] or ""
            if ts > last.get(kid, ""):
                last[kid] = ts

    total = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
    never = total - len(counts)

    result = {
        "retrieved_unique": len(counts),
        "total_entries": total,
        "never_retrieved": never,
        "never_retrieved_ratio": round(never / total, 4) if total else 0,
        "top_retrieved": counts.most_common(10),
        "dry_run": dry_run,
    }
    if not dry_run and counts:
        with conn:
            conn.execute("UPDATE knowledge_base SET retrieval_count=0, last_retrieved_at=''")
            conn.executemany(
                "UPDATE knowledge_base SET retrieval_count=?, last_retrieved_at=? WHERE id=?",
                [(c, last.get(k, ""), k) for k, c in counts.items()])
    return result


# ══════════════════════════════════════════════════════════════
# 框架路由：审查框架 → 流派偏置
# ══════════════════════════════════════════════════════════════
#
# 知识库不是中立的资料库，它是一堆互相矛盾的方法论放在同一个向量空间里，
# 按语义相似度投票。用户问「短线」，出来的是技术派；问「长期持有」，
# 出来的是价值派。同一个系统给出相反建议，且**不自称立场**。
#
# 修复方式不是删掉一派，而是：
#   1. 每条证据在上下文里标注流派（见 rag.py 的 [流派:xx] 标记）
#   2. 调用方声明当前审查框架时，按框架做温和偏置
#   3. 召回到互相矛盾的流派时，显式向 LLM 披露冲突
#
# 偏置幅度刻意做小（±0.5 以内，乘性），因为流派是「解读视角」，
# 不是「相关性」。相关性高的技术派内容，对价值框架依然有信息量
# （可以当反例读），不该被彻底压掉。

FRAMEWORK_SCHOOL_BIAS = {
    # 芒格式价值审查：质量优先于价格，反对频繁交易
    "value": {
        "value": 0.30, "history": 0.18, "asset": 0.10, "behavioral": 0.12,
        "quant": 0.02, "macro": 0.02, "general": 0.05, "technical": -0.30,
    },
    # 交易/择时框架
    "trading": {
        "technical": 0.30, "behavioral": 0.12, "quant": 0.08, "value": 0.05,
        "history": 0.02, "asset": 0.02, "macro": 0.02, "general": 0.05,
    },
    # 量化/组合框架
    "quant": {
        "quant": 0.30, "history": -0.05, "technical": 0.08, "value": 0.05,
        "macro": 0.08, "asset": 0.08, "behavioral": 0.05, "general": 0.05,
    },
    # 宏观/周期框架
    "macro": {
        "macro": 0.30, "history": 0.12, "quant": 0.08, "asset": 0.08,
        "value": 0.05, "technical": 0.02, "behavioral": 0.02, "general": 0.05,
    },
    # 不偏置：只标注，不排序
    "neutral": {},
}

# 结论方向可能相反的方法论组合 —— 同时被召回时必须向 LLM 披露
CONFLICTING_SCHOOL_PAIRS = (
    ("value", "technical"),   # 长期持有 vs 择时交易
    ("value", "quant"),       # 基本面定价 vs 因子/统计套利
    ("technical", "macro"),   # 图表信号 vs 宏观驱动
)


def school_bias(school: str, framework: str) -> float:
    """返回流派在给定框架下的分数偏置（乘性修正量，量级 ±0.3）。"""
    table = FRAMEWORK_SCHOOL_BIAS.get(framework or "neutral")
    if not table:
        return 0.0
    return float(table.get(school or "", 0.0))


def detect_school_conflict(schools: list[str]) -> list[tuple[str, str]]:
    """检测召回结果中互相矛盾的流派组合。"""
    present = {s for s in schools if s}
    return [(a, b) for a, b in CONFLICTING_SCHOOL_PAIRS if a in present and b in present]


def framework_header(school_counts: dict, framework: str,
                     conflicts: list[tuple[str, str]]) -> str:
    """生成注入上下文的流派披露块。

    这是「两套知识给出相反建议」这个问题的正面解法：
    不隐瞒分歧，也不让分歧在 LLM 内部悄悄融合成一段自洽的错话。
    """
    if not school_counts:
        return ""
    total = sum(school_counts.values())
    if total == 0:
        return ""

    ordered = sorted(school_counts.items(), key=lambda kv: kv[1], reverse=True)
    parts = [f"{SCHOOL_LABELS.get(k, k)}{v}条" for k, v in ordered if v]
    fw_label = {
        "value": "价值（质量优先、长期持有）",
        "trading": "交易（择时、趋势）",
        "quant": "量化（因子、统计）",
        "macro": "宏观（周期、政策）",
        "neutral": "未指定（仅标注，不偏置）",
    }.get(framework or "neutral", framework or "neutral")

    lines = [
        "[知识库流派披露]",
        f"本次召回: {'、'.join(parts)}。",
        f"当前审查框架: {fw_label}。",
    ]
    if conflicts:
        pairs = "；".join(
            f"{SCHOOL_LABELS.get(a, a)} 与 {SCHOOL_LABELS.get(b, b)}" for a, b in conflicts
        )
        lines.append(
            f"注意: 召回内容同时包含 {pairs}，两派方法论在「该不该择时」「该不该长期持有」"
            f"等问题上结论通常相反。请按当前框架取向组织论证；若引用对立流派观点，"
            f"必须显式标明出处流派，不得把两派论点混成一段统一结论。"
        )
    lines.append("每条证据末尾的 [流派:xx] 标识其方法论出处。")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 中文实体消歧：金融词的「形容词用法」守卫
# ══════════════════════════════════════════════════════════════
#
# 问题：查「黄金」会召回「止损执行的黄金法则」「预训练数据规模的黄金三角」。
# 「黄金」在这里是形容词（意为宝贵），不是资产。
# FTS5 的短语匹配无法区分，只能靠共现模式：如果某个词的全部出现位置
# 都落在已知固定搭配内部，那它在这个文档里就不是实体。
#
# 这类固定搭配无法穷举，但高频的就那几个；漏掉的代价只是召回噪声，
# 不会误杀真实体（因为真实体必然有发生在搭配之外的独立出现）。

IDIOM_GUARD: dict[str, tuple[str, ...]] = {
    "黄金": ("黄金法则", "黄金三角", "黄金窗口", "黄金时期", "黄金时期",
             "黄金十年", "黄金时代", "黄金坑", "黄金比例", "黄金分割",
             "黄金周", "黄金档", "黄金分割线", "黄金标准"),
    "龙头": ("水龙头", "龙头股", "龙头老大"),
    "白马": ("白马王子", "白马股"),
    "黑马": ("黑马股",),
    "龙头股": (),
}


def entity_occurrences(text: str, term: str) -> tuple[int, int]:
    """返回 (总出现次数, 独立出现次数)。独立 = 不在已知固定搭配内部。"""
    if not text or not term:
        return 0, 0
    total = text.count(term)
    if total == 0:
        return 0, 0

    idioms = IDIOM_GUARD.get(term, ())
    if not idioms:
        return total, total

    masked = text
    for idiom in idioms:
        if term in idiom and idiom != term:
            masked = masked.replace(idiom, "\x00" * len(idiom))
    independent = masked.count(term)
    return total, independent


def term_is_entity_only(text: str, term: str) -> bool:
    """词在该文本中是否只以固定搭配形式出现（即不是实体用法）。"""
    total, independent = entity_occurrences(text, term)
    return total > 0 and independent == 0


# ══════════════════════════════════════════════════════════════
# 运行时查询（检索层使用）
# ══════════════════════════════════════════════════════════════

_META_CACHE: dict[str, dict] = {}
_META_CACHE_TS = 0.0
_META_CACHE_TTL = 120.0


def get_entry_meta_map(force: bool = False) -> dict[str, dict]:
    """返回 {(category, reference_id): metadata}，供检索层做域过滤与流派标注。

    缓存 2 分钟。8852 行的三列查询开销可忽略，但仍走缓存以避免每次检索都查库。
    """
    global _META_CACHE, _META_CACHE_TS
    import time
    now = time.time()
    if not force and _META_CACHE and now - _META_CACHE_TS < _META_CACHE_TTL:
        return _META_CACHE

    from db._conn import _get_conn
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, category, domain, school, temporal_type, as_of_date,
                   usefulness_score
            FROM knowledge_base
        """).fetchall()
        meta = {}
        for r in rows:
            meta[f"{r['category']}:{r['id']}"] = {
                "domain": r["domain"] or "",
                "school": r["school"] or "",
                "temporal_type": r["temporal_type"] or "",
                "as_of_date": r["as_of_date"] or "",
                "usefulness_score": r["usefulness_score"],
            }
        _META_CACHE = meta
        _META_CACHE_TS = now
        return meta
    except Exception as exc:
        logger.warning(f"读取知识库元数据失败: {exc}")
        return _META_CACHE or {}
    finally:
        conn.close()


def invalidate_meta_cache():
    global _META_CACHE_TS
    _META_CACHE_TS = 0.0


def governance_report(conn: sqlite3.Connection) -> dict:
    """治理现状报告：哪些维度已覆盖、哪些还是空的。"""
    def _group(col: str) -> dict:
        out = {}
        for row in conn.execute(
            f"SELECT COALESCE(NULLIF({col},''), '(空)') k, COUNT(*) c "
            f"FROM knowledge_base GROUP BY k ORDER BY c DESC"
        ):
            out[row[0]] = row[1]
        return out

    total = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
    fill = {}
    for col in ("domain", "school", "temporal_type", "as_of_date", "usefulness_score"):
        filled = conn.execute(
            f"SELECT COUNT(*) FROM knowledge_base WHERE COALESCE(CAST({col} AS TEXT),'') NOT IN ('','0')"
        ).fetchone()[0]
        fill[col] = {"filled": filled, "total": total,
                     "ratio": round(filled / total, 4) if total else 0.0}

    # category 与 atom_type 的矛盾项
    contradictions = conn.execute("""
        SELECT COUNT(*) FROM knowledge_base
        WHERE atom_type='user_lesson' AND category != 'user_lesson'
    """).fetchone()[0]

    return {
        "total": total,
        "fill": fill,
        "domain": _group("domain"),
        "school": _group("school"),
        "temporal_type": _group("temporal_type"),
        "usefulness_distribution": _group("usefulness_score"),
        "contradictions": {"user_lesson_in_wrong_category": contradictions},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
