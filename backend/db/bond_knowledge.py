"""债券知识库 + 投资策略知识 + 债券市场数据。"""

import json
import os

from db._conn import _get_conn


def seed_bond_knowledge():
    try:
        """将债券知识写入 skill_documents 表（如尚未写入）。"""
        conn = _get_conn()
        existing = conn.execute(
            "SELECT COUNT(*) FROM skill_documents WHERE doc_type = 'bond_knowledge'"
        ).fetchone()[0]
        if existing > 0:
            conn.close()
            return False
        md_path = os.path.join(os.path.dirname(__file__), "bond_knowledge.md")
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            conn.close()
            return False
        conn.execute(
            "INSERT INTO skill_documents (doc_type, content) VALUES (?, ?)",
            ("bond_knowledge", content),
        )
        conn.commit()
        conn.close()
        return True
    finally:
        conn.close()


def get_bond_market_data() -> dict | None:
    try:
        """获取债券市场最新数据快照。"""
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM bond_market_data ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return None
        data = dict(row)
        try:
            data["content"] = json.loads(data["content"])
        except (json.JSONDecodeError, TypeError):
            pass
        return data
    finally:
        conn.close()


def save_bond_market_data(data_type: str, content: dict, snapshot_date: str = None):
    try:
        """保存债券市场数据快照。"""
        if not snapshot_date:
            from datetime import date
            snapshot_date = date.today().isoformat()
        conn = _get_conn()
        conn.execute(
            "INSERT INTO bond_market_data (data_type, content, snapshot_date) VALUES (?, ?, ?)",
            (data_type, json.dumps(content, ensure_ascii=False), snapshot_date),
        )
        conn.commit()
        conn.close()
        return True
    finally:
        conn.close()


def seed_investment_strategy_knowledge():
    try:
        """将投资策略知识写入 skill_documents 表（如尚未写入）。"""
        conn = _get_conn()
        existing = conn.execute(
            "SELECT COUNT(*) FROM skill_documents WHERE doc_type = 'investment_strategy'"
        ).fetchone()[0]
        if existing > 0:
            conn.close()
            return False

        content = """# 4%定投法（强化版）— 研究员雷牛牛

    ## 核心规则

    4%定投法是一种基于回撤幅度的纪律性建仓方法，核心思想是：**只有当标的从上次买入价下跌达到指定幅度（如4%）时才出手买入**，以此积累安全边际。

    ### 基本操作规则
    1. **首次建仓**：在估值合理或偏低时首次买入，记录成本价
    2. **下跌加仓**：当价格从上次买入价下跌 4% 时，执行下一次买入
    3. **绝不追涨**：坚决不在暴力上涨时追涨，上涨时耐心等待
    4. **纪律执行**：严格按规则执行，不因情绪改变计划

    ### 强化版规则（适用于连续下跌场景）
    5. **跌幅越大，买入越多**：下跌 4% 买 1 份，下跌 8% 买 2 份，下跌 12% 买 3 份（金字塔加仓）
    6. **设置最大仓位上限**：单品种仓位不超过总资产的 20-30%，防止过度集中
    7. **估值极端时加码**：当估值百分位低于 10%（深度低估），可将每次买入份数翻倍
    8. **保留现金储备**：始终保留 20-30% 现金，确保有子弹继续加仓

    ## 应用场景

    ### 场景1：持仓连续亏损
    当用户持仓出现连续亏损时，应用 4% 定投法的思路：
    - 不要恐慌割肉，而是评估是否值得继续加仓
    - 如果标的基本面没问题、估值已进入低估区间，下跌反而是加仓机会
    - 按 4% 间隔分批加仓，摊低成本，积累安全边际
    - 计算还需要多少次加仓才能回盈，给用户信心

    ### 场景2：新建仓计划
    - 先确定目标仓位和分批计划
    - 首次买入 1/3 仓位
    - 后续按 4% 下跌间隔逐步加仓至满仓
    - 如果买入后直接上涨，不追涨，等待回调或转向其他低估品种

    ### 场景3：市场整体下跌
    - 评估哪些品种跌幅大但基本面没变
    - 优先加仓跌幅大、估值低的品种
    - 分散加仓，不要集中在单一品种

    ## 关键原则

    1. **安全边际**：每次下跌买入都在积累安全边际，成本越低，未来盈利空间越大
    2. **纪律性**：排除情绪干扰，机械执行，这是该方法最大的优势
    3. **逆向思维**：别人恐惧时贪婪，但贪婪要有纪律——按计划加仓，不是一把梭
    4. **仓位管理**：永远留有余地，不要在一次下跌中打光所有子弹
    5. **耐心**：好的价格往往带着"鬼故事"一起来，这是市场给忍耐者的奖赏

    ## 计算示例

    假设某指数当前估值百分位 25%（偏低估），首次买入价 1.000：
    - 第1次加仓：0.960（下跌4%），买入1份
    - 第2次加仓：0.922（累计下跌8%），买入2份
    - 第3次加仓：0.885（累计下跌12%），买入3份
    - 平均成本：约 0.935
    - 当价格回到 0.935 即可回盈，而非需要回到 1.000

    ## 与估值结合

    4%定投法必须与估值分析结合使用：
    - **低估区域（<40%百分位）**：适合启动4%定投法，积极建仓
    - **合理区域（40%-60%百分位）**：持有观望，不加仓也不减仓
    - **高估区域（>60%百分位）**：停止加仓，开始考虑分批止盈
    - **深度低估（<10%百分位）**：加码买入，这是难得的机会

    ## 风险提示

    1. 4%定投法不适用于基本面恶化的品种（如行业衰退、政策打压）
    2. 需要足够的现金储备支撑持续加仓
    3. 可能面临长期浮亏，需要心理准备
    4. 止损线：如果基本面发生根本变化，应果断止损而非机械加仓
    """

        conn.execute(
            "INSERT INTO skill_documents (doc_type, content) VALUES (?, ?)",
            ("investment_strategy", content),
        )
        conn.commit()
        conn.close()

        # 索引到 RAG
        try:
            from services.rag import index_skill_document
            conn2 = _get_conn()
            doc_row = conn2.execute("SELECT id FROM skill_documents WHERE doc_type = 'investment_strategy'").fetchone()
            conn2.close()
            if doc_row:
                index_skill_document(doc_row[0], "4%定投法（强化版）— 研究员雷牛牛", content)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"索引4%定投法到RAG失败: {e}")

        return True
    finally:
        conn.close()

