"""知识库治理层的确定性回归。

覆盖三件事：
1. 领域/流派/时序判定的规则边界（纯函数，无 IO）
2. 中文实体消歧：金融词的形容词用法不能被当实体召回
3. 用户教训生成：数字必须来自真实表，且幂等

这些规则决定了「检索到的证据是否可信」，一旦回归，症状是
「答案看起来没变，但依据变成了错的」—— 最难靠肉眼发现的故障类型。
"""

import sqlite3

import pytest

from services import knowledge_governance as gov
from services import user_lessons


# ══════════════════════════════════════════════════════════════
# 领域判定
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("category,source,expected", [
    # 书库内均为投资书目
    ("book", "聪明的投资者", gov.DOMAIN_INVESTMENT),
    # 来源路径命中 AI 工程书目标记
    ("note", "obsidian://04-Resources/书籍笔记/AI工程大模型应用开发实战/流式输出.md",
     gov.DOMAIN_AI_ENGINEERING),
    ("note", "obsidian://04-Resources/书籍笔记/LangChain核心技术与LLM项目实践/LCEL.md",
     gov.DOMAIN_AI_ENGINEERING),
    # 结构化知识默认投资域
    ("concept", "", gov.DOMAIN_INVESTMENT),
    ("strategy", "", gov.DOMAIN_INVESTMENT),
    ("fund", "", gov.DOMAIN_INVESTMENT),
])
def test_infer_domain_by_source(category, source, expected):
    assert gov.infer_domain(category, source, "标题", "正文") == expected


def test_infer_domain_uses_content_markers_when_source_is_silent():
    ai_text = "使用 LangChain 与 FAISS 构建向量数据库，并通过 PromptTemplate 注入 Embedding"
    assert gov.infer_domain("note", "", "检索", ai_text) == gov.DOMAIN_AI_ENGINEERING

    fin_text = "白酒行业估值处于历史低位，市盈率与股息率显示安全边际较高"
    assert gov.infer_domain("note", "", "白酒", fin_text) == gov.DOMAIN_INVESTMENT


def test_infer_domain_ko_for_undecidable_content():
    """既无 AI 也无金融标记时标 other，而不是硬塞进投资域。"""
    assert gov.infer_domain("note", "", "随手记", "今天天气不错，出门走了两万步") == gov.DOMAIN_OTHER


# ══════════════════════════════════════════════════════════════
# 时序判定
# ══════════════════════════════════════════════════════════════

def test_extract_years_ignores_malformed():
    assert gov.extract_years("1999年8月金价251.9美元，2008年破1000") == [1999, 2008]
    # 5 位数不应被当成 4 位年份的片段
    assert 20260 not in gov.extract_years("订单号 20260123")


def test_dated_data_uses_mode_year_not_max_year():
    """众数优先：偶发提到的年份不能顶掉数据锚点。"""
    content = "数据显示，1999年至2001年间金价低位徘徊；2016年英退欧公投引发避险，1999年再次被引用。"
    ttype, as_of = gov.infer_temporal("data", content)
    assert ttype == gov.TEMPORAL_DATED
    assert as_of == "1999"      # 1999 出现 2 次，2016 出现 1 次


def test_tie_on_year_picks_earlier():
    ttype, as_of = gov.infer_temporal("data", "2008年数据与2016年数据对比")
    assert (ttype, as_of) == (gov.TEMPORAL_DATED, "2008")


def test_data_without_year_but_empirical_claim_is_undated():
    ttype, as_of = gov.infer_temporal("data", "统计显示该策略在多数年份跑赢基准指数")
    assert ttype == gov.TEMPORAL_UNDATED
    assert as_of == ""


def test_definition_without_year_is_timeless():
    """定义/算例不是实证数据，不应被标成「缺时点」。"""
    ttype, _ = gov.infer_temporal(
        "data", "市净率是每股股价与每股净资产的比率，例如市盈率为8时盈利收益率为12.5%")
    assert ttype == gov.TEMPORAL_TIMELESS


def test_policy_is_regime_dependent():
    ttype, _ = gov.infer_temporal("policy", "监管要求货币基金投资组合久期不超过120天")
    assert ttype == gov.TEMPORAL_REGIME_DEPENDENT


def test_principle_is_timeless():
    assert gov.infer_temporal("principle", "安全边际是价值投资的核心")[0] == gov.TEMPORAL_TIMELESS


# ══════════════════════════════════════════════════════════════
# 原子类型修复
# ══════════════════════════════════════════════════════════════

def test_book_cannot_hold_user_lesson():
    new, reason = gov.repair_atom_type(
        "book", "user_lesson", "这本书讲了长期持有的道理，没有具体数字。")
    assert new == "insight"
    assert "user_lesson" in reason


def test_quantitative_excerpt_becomes_data():
    new, _ = gov.repair_atom_type(
        "book", "user_lesson", "数据显示年化收益 12.5%，最大回撤 30%，持续 2015年到2019年。")
    assert new == "data"


def test_definition_title_demotes_data_to_concept():
    new, reason = gov.repair_atom_type(
        "book", "data", "市盈率是价格与盈利的比值，例如 8 倍对应 12.5%。", title="市盈率的概念")
    assert new == "concept"
    assert "定义" in reason


def test_valid_atom_type_untouched():
    new, reason = gov.repair_atom_type("book", "rule", "不要在亏损时加仓", title="加仓规则")
    assert (new, reason) == ("rule", "")


def test_user_lesson_in_correct_category_untouched():
    new, reason = gov.repair_atom_type("user_lesson", "user_lesson", "实际交易记录")
    assert (new, reason) == ("user_lesson", "")


# ══════════════════════════════════════════════════════════════
# 中文实体消歧
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,expected", [
    ("止损执行的黄金法则：立即行动，不抱幻想", True),
    ("预训练数据规模的黄金三角", True),
    ("黄金价格与美元通常呈负相关", False),
    ("黄金法则之外，黄金本身也是避险资产", False),   # 有独立出现即算实体
    ("该基金配置黄金 ETF 以对冲通胀", False),
])
def test_entity_guard_gold(text, expected):
    assert gov.term_is_entity_only(text, "黄金") is expected


def test_entity_guard_ignores_unknown_terms():
    """未配置固定搭配的词，出现即为实体，不做猜测。"""
    assert gov.term_is_entity_only("白酒行业景气度下降", "白酒") is False


def test_entity_occurrences_counts():
    total, independent = gov.entity_occurrences("黄金法则与黄金三角，最后才提黄金", "黄金")
    assert total == 3
    assert independent == 1


# ══════════════════════════════════════════════════════════════
# 框架路由与冲突披露
# ══════════════════════════════════════════════════════════════

def test_value_framework_penalises_technical():
    assert gov.school_bias("value", "value") > 0
    assert gov.school_bias("technical", "value") < 0
    # 偏置幅度必须小：流派是解读视角，不是相关性
    for school in gov.SCHOOL_LABELS:
        assert abs(gov.school_bias(school, "value")) <= 0.30


def test_neutral_framework_has_no_bias():
    for school in gov.SCHOOL_LABELS:
        assert gov.school_bias(school, "neutral") == 0


def test_conflict_detection():
    assert gov.detect_school_conflict(["value", "technical"]) == [("value", "technical")]
    assert gov.detect_school_conflict(["value", "history", "macro"]) == []


def test_framework_header_discloses_conflict():
    header = gov.framework_header(
        {"value": 3, "technical": 2}, "value", [("value", "technical")])
    assert "流派披露" in header
    assert "价值3条" in header
    assert "结论通常相反" in header
    # 必须要求 LLM 标明出处流派，否则两派会被无声融合
    assert "标明出处流派" in header


def test_framework_header_empty_when_no_schools():
    assert gov.framework_header({}, "value", []) == ""


# ══════════════════════════════════════════════════════════════
# 用户教训生成（内存库，不碰生产数据）
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def lesson_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE portfolio_holdings (
            fund_code TEXT, fund_name TEXT, account TEXT, index_name TEXT,
            fund_category TEXT, shares REAL, cost_price REAL, total_cost REAL,
            current_value REAL, profit_loss REAL, profit_rate REAL, buy_date TEXT);
        CREATE TABLE portfolio_transactions (
            fund_code TEXT, fund_name TEXT, account TEXT, transaction_type TEXT,
            amount REAL, shares REAL, price REAL, transaction_date TEXT,
            status TEXT, is_hypothetical INTEGER, is_system INTEGER);
        CREATE TABLE knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, subcategory TEXT,
            title TEXT, content TEXT, source TEXT, keywords TEXT, importance INTEGER,
            atom_type TEXT, evidence_level TEXT, as_of_date TEXT, temporal_type TEXT,
            domain TEXT, school TEXT, limitations TEXT, counterpoints TEXT,
            usefulness_score INTEGER);
    """)
    yield conn
    conn.close()


def _seed(conn):
    conn.executemany(
        "INSERT INTO portfolio_holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("A001", "深套基金A", "主账户", "中证白酒指数", "index",
             100.0, 1.0, 10000.0, 7000.0, -3000.0, -0.30, "2026-05-01"),
            ("A002", "浅亏基金B", "主账户", "沪深300", "index",
             100.0, 1.0, 5000.0, 4800.0, -200.0, -0.04, ""),
            # 已清仓：成本与份额为 0
            ("A003", "已清仓C", "主账户", "", "index", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "2026-01-01"),
        ])
    conn.executemany(
        "INSERT INTO portfolio_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            # 3 笔真实买入
            ("A001", "深套基金A", "主账户", "buy", 10000.0, 100.0, 1.0, "2026-05-01", "confirmed", 0, 0),
            ("A002", "浅亏基金B", "主账户", "buy", 5000.0, 100.0, 1.0, "2026-06-01", "confirmed", 0, 0),
            ("A002", "浅亏基金B", "主账户", "buy", 100.0, 2.0, 1.0, "2026-07-01", "pending", 0, 0),
            # 1 笔真实卖出，金额为 0（记账异常）
            ("A003", "已清仓C", "主账户", "sell", 0.0, None, None, "2026-08-01", "confirmed", 0, 0),
            # 模拟交易：必须被排除
            ("A001", "深套基金A", "主账户", "buy", 999999.0, 1.0, 1.0, "2026-09-01", "confirmed", 1, 0),
        ])
    conn.commit()


def test_lessons_exclude_hypothetical_and_pending(lesson_db):
    _seed(lesson_db)
    lessons = user_lessons.build_lessons(lesson_db)
    titles = {l["title"]: l for l in lessons}

    trade_lesson = next(l for l in lessons if "买入" in l["title"] and "卖出" in l["title"])
    # 真实成交只有 2 笔买入（pending 不计），1 笔卖出
    assert "买入 2 笔" in trade_lesson["content"]
    assert "卖出 1 笔" in trade_lesson["content"]
    # 模拟金额 999999 绝不能进入真实口径
    assert "999,999" not in trade_lesson["content"]

    # 模拟交易自身要被点名为数据质量风险
    hypo = [l for l in lessons if "模拟交易" in l["title"]]
    assert hypo and "1 笔" in hypo[0]["title"]


def test_lessons_state_real_numbers(lesson_db):
    _seed(lesson_db)
    lessons = {l["title"]: l for l in user_lessons.build_lessons(lesson_db)}

    overall = next(l for l in lessons.values() if l["subcategory"] == "组合状态")
    # 成本 15000，现值 11800
    assert "15,000" in overall["content"]
    assert "11,800" in overall["content"]
    assert "-21.3%" in overall["title"] or "-21.33%" in overall["title"]

    deep = next(l for l in lessons.values() if "浮亏超 15%" in l["title"])
    assert "深套基金A" in deep["content"]
    assert "浅亏基金B" not in deep["content"]      # -4% 不算深套


def test_lessons_flag_data_quality_gaps(lesson_db):
    _seed(lesson_db)
    lessons = {l["title"]: l for l in user_lessons.build_lessons(lesson_db)}

    assert any("缺少建仓日期" in t for t in lessons), "buy_date 缺失必须成为一条教训"
    assert any("已清仓" in t for t in lessons), "已清仓仍留表必须被点名"
    assert any("金额为 0" in t for t in lessons), "零金额赎回必须被点名"


def test_lessons_are_idempotent(lesson_db):
    _seed(lesson_db)
    first = user_lessons.sync_lessons(lesson_db, dry_run=False)
    assert first["new"] > 0

    second = user_lessons.sync_lessons(lesson_db, dry_run=False)
    assert second["new"] == 0
    assert second["skipped_existing"] == first["new"]

    total = lesson_db.execute(
        "SELECT COUNT(*) FROM knowledge_base WHERE category='user_lesson'").fetchone()[0]
    assert total == first["new"]


def test_dry_run_never_writes(lesson_db):
    _seed(lesson_db)
    result = user_lessons.sync_lessons(lesson_db, dry_run=True)
    assert result["dry_run"] is True
    assert result["new"] > 0
    total = lesson_db.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
    assert total == 0


def test_generated_lessons_carry_governance_fields(lesson_db):
    """新生成的教训必须自带治理字段，否则又成治理盲区。"""
    _seed(lesson_db)
    user_lessons.sync_lessons(lesson_db, dry_run=False)
    rows = lesson_db.execute("SELECT * FROM knowledge_base WHERE category='user_lesson'").fetchall()
    for r in rows:
        assert r["domain"] == "investment"
        assert r["atom_type"] == "user_lesson"
        assert r["evidence_level"] == "user_memory"
        assert r["as_of_date"], "教训必须带时点"
        assert r["usefulness_score"] and r["usefulness_score"] >= 5
