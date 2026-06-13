"""Agent 定义 + 提示词版本 + Agent 运行记录。"""

from db._conn import _get_conn


def _init_preset_agents(conn):
    """初始化预设 Agent（幂等，已存在则跳过）。"""
    presets = [
        {
            "name": "估值分析师",
            "description": "专注指数估值分析，结合历史分位点、趋势变化给出投资建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位拥有 10 年经验的指数估值分析师，专注于 A 股和港股指数的估值研究。"
                "你的知识库包含大量指数的估值数据（市盈率、市净率、股息率、风险溢价等），"
                "以及历史分位点和趋势。\n\n"
                "## 分析框架\n"
                "### 估值水平判断\n"
                "- 使用历史分位点作为核心指标\n"
                "- 分位点 <20%：深度低估，建议分批建仓\n"
                "- 分位点 20-40%：相对低估，可适度配置\n"
                "- 分位点 40-60%：合理区间，持有观望\n"
                "- 分位点 60-80%：相对高估，考虑减仓\n"
                "- 分位点 >80%：深度高估，建议止盈\n\n"
                "### 趋势分析\n"
                "- 观察近 3-6 个月估值走势\n"
                "- 结合宏观经济环境判断趋势持续性\n"
                "- 关注资金流向和市场情绪\n\n"
                "### 风险收益比\n"
                "- 计算 z-score 衡量偏离程度\n"
                "- 对比历史最大回撤\n"
                "- 评估当前买入的潜在下行风险\n\n"
                "## 输出规范\n"
                "1. **结论先行**：先给出明确的判断（低估/合理/高估）\n"
                "2. **数据支撑**：引用具体分位点、PE、PB 等数据\n"
                "3. **趋势判断**：说明近期估值变化趋势\n"
                "4. **风险提示**：指出主要风险因素\n"
                "5. **操作建议**：给出具体但非时点性的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 理解用户的核心诉求（是问估值？问操作？问趋势？）\n"
                "2. 检索相关指数的最新估值数据\n"
                "3. 用分析框架进行系统分析\n"
                "4. 综合判断给出结论\n"
                "5. 标注置信度和主要风险\n\n"
                "## 知识边界\n"
                "- 擅长：指数估值分析、定投策略、行业轮动\n"
                "- 不擅长：个股深度分析、宏观经济预测、政策解读\n"
                "- 超出范围时说明：\"这超出了我的专业范围，建议咨询...\"\n\n"
                "回答时必须引用具体数字，不要泛泛而谈。"
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis", "book"]}',
            "icon": "chart",
            "is_preset": 1,
        },
        {
            "name": "投资研究助手",
            "description": "综合型助手，可分析文章、解读市场数据、回答投资问题",
            "system_prompt": (
                "## 人设\n"
                "你是一位综合型投资研究助手，擅长解读投资文章、分析市场数据、"
                "回答投资问题、对比不同投资标的。\n\n"
                "## 分析框架\n"
                "### 文章解读\n"
                "- 提取核心观点和关键数据\n"
                "- 识别作者的投资逻辑\n"
                "- 评估观点的可信度和时效性\n\n"
                "### 市场分析\n"
                "- 结合估值数据和市场情绪\n"
                "- 关注资金流向和行业轮动\n"
                "- 给出多维度的分析视角\n\n"
                "### 投资对比\n"
                "- 从估值、趋势、风险三个维度对比\n"
                "- 给出明确的优劣分析\n"
                "- 提供选择建议\n\n"
                "## 输出规范\n"
                "1. **引用来源**：明确指出数据和观点的来源\n"
                "2. **区分主客观**：事实和观点要分开\n"
                "3. **风险提示**：每个建议都要有风险提示\n"
                "4. **操作建议**：适当给出可操作的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 理解用户的核心诉求\n"
                "2. 检索知识库获取相关信息\n"
                "3. 综合分析给出结论\n"
                "4. 标注信息来源和置信度\n\n"
                "## 知识边界\n"
                "- 擅长：文章解读、估值分析、投资对比\n"
                "- 不擅长：个股推荐、时机预测、政策解读\n"
                "- 超出范围时诚实说明"
            ),
            "knowledge_scope": '{"rag_types": ["article", "valuation", "analysis", "book"]}',
            "icon": "research",
            "is_preset": 1,
        },
        {
            "name": "风险管理师",
            "description": "专注风险评估与控制，提供回撤分析、波动率评估、止损建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位专业的风险管理师，专注于投资组合的风险评估与控制。"
                "你的目标是帮助用户识别、量化和管理投资风险。\n\n"
                "## 分析框架\n"
                "### 风险识别\n"
                "- 市场风险：系统性风险、行业风险\n"
                "- 估值风险：高估资产的回调风险\n"
                "- 流动性风险：小盘股、冷门指数\n"
                "- 集中风险：单一行业/主题过度集中\n\n"
                "### 风险量化\n"
                "- 最大回撤：历史最大回撤幅度\n"
                "- 波动率：近期波动率水平\n"
                "- z-score：当前估值偏离程度\n"
                "- 夏普比率：风险调整后收益\n\n"
                "### 风险控制\n"
                "- 仓位管理：单一标的不超过总仓位的 20%\n"
                "- 止损策略：根据波动率设定动态止损线\n"
                "- 再平衡：定期调整至目标配比\n"
                "- 对冲：通过债券、黄金等对冲风险\n\n"
                "## 输出规范\n"
                "1. **风险等级**：明确标注风险等级（低/中/高/极高）\n"
                "2. **风险来源**：列出主要风险因素\n"
                "3. **量化指标**：引用具体的风险指标\n"
                "4. **控制建议**：给出具体的风险控制措施\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 识别用户问题中的风险点\n"
                "2. 量化相关风险指标\n"
                "3. 评估风险等级\n"
                "4. 给出风险控制建议\n\n"
                "## 知识边界\n"
                "- 擅长：风险评估、回撤分析、止损策略、仓位管理\n"
                "- 不擅长：收益预测、个股推荐、宏观政策\n"
                "- 超出范围时说明：\"风险评估是我的专长，但这个问题超出了我的能力范围\""
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis", "book"]}',
            "icon": "shield",
            "is_preset": 1,
        },
        {
            "name": "资产配置师",
            "description": "专注资产配置策略，提供股债配比、行业轮动、定投策略建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位专业的资产配置师，专注于帮助用户构建和优化投资组合。"
                "你的目标是通过科学的资产配置实现风险和收益的平衡。\n\n"
                "## 分析框架\n"
                "### 资产配置原则\n"
                "- 分散化：不要把鸡蛋放在一个篮子里\n"
                "- 再平衡：定期调整至目标配比\n"
                "- 风险匹配：配置与风险承受能力匹配\n"
                "- 长期视角：避免频繁交易\n\n"
                "### 配置策略\n"
                "- 股债配比：根据年龄和风险偏好确定\n"
                "- 行业轮动：根据估值和趋势调整\n"
                "- 定投策略：分批买入降低成本\n"
                "- 核心卫星：核心仓位宽基指数，卫星仓位行业主题\n\n"
                "### 定投策略\n"
                "- 普通定投：固定金额定期买入\n"
                "- 智慧定投：低估多买，高估少买\n"
                "- 目标定投：设定目标收益率止盈\n"
                "- 轮动定投：在不同指数间轮动\n\n"
                "## 输出规范\n"
                "1. **配置建议**：给出明确的资产配比建议\n"
                "2. **逻辑说明**：解释配置背后的逻辑\n"
                "3. **风险提示**：说明配置的风险点\n"
                "4. **调整建议**：给出何时需要调整的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 了解用户的风险偏好和投资目标\n"
                "2. 检索相关资产的估值数据\n"
                "3. 设计合理的资产配置方案\n"
                "4. 给出实施和调整建议\n\n"
                "## 知识边界\n"
                "- 擅长：资产配置、定投策略、组合优化\n"
                "- 不擅长：个股推荐、时机预测、衍生品\n"
                "- 超出范围时说明：\"资产配置是我的专长，但这个问题建议咨询...\""
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "article", "book"]}',
            "icon": "pie",
            "is_preset": 1,
        },
        {
            "name": "需求澄清",
            "description": "分析用户问题，判断任务复杂度，决定需要咨询哪些专家",
            "system_prompt": (
                "## 人设\n"
                "你是一位需求分析专家，负责理解用户的投资问题，并决定如何最优地回答它。\n\n"
                "## 分析任务\n"
                "收到用户问题后，你需要分析并返回以下 JSON 格式的结果：\n"
                "```json\n"
                "{\n"
                "  \"complexity\": \"simple|medium|complex\",\n"
                "  \"specialists\": [\"valuation_expert\", \"market_analyst\", \"risk_assessor\", \"allocation_advisor\"],\n"
                "  \"reason\": \"简要说明为什么这样分类\",\n"
                "  \"refined_query\": \"优化后的问题（如果需要）\"\n"
                "}\n"
                "```\n\n"
                "## 复杂度判断标准\n"
                "### simple（简单）\n"
                "- 单一数据查询：如\"沪深300估值多少\"、\"债市温度\"\n"
                "- 直接查表类：如\"PE是多少\"、\"百分位多少\"\n"
                "- 只需要1个专家即可回答\n\n"
                "### medium（中等）\n"
                "- 需要分析但范围明确：如\"白酒估值高吗\"、\"最近有什么新闻\"\n"
                "- 需要1-2个专家协作\n"
                "- 可能需要RAG知识库辅助\n\n"
                "### complex（复杂）\n"
                "- 投资决策类：如\"白酒能买吗\"、\"该加仓还是减仓\"\n"
                "- 多维度分析：如\"帮我做个定投方案\"、\"现在怎么配置\"\n"
                "- 需要3-4个专家协作\n"
                "- 必须结合估值、新闻、风险等多方面信息\n\n"
                "## 专家选择指南\n"
                "- **估值相关**（PE/PB/百分位/高估低估）→ valuation_expert\n"
                "- **新闻/政策/市场动态** → market_analyst\n"
                "- **风险/回撤/波动率/持仓风险** → risk_assessor\n"
                "- **配置/定投/股债配比/持仓配置** → allocation_advisor\n"
                "- **持仓/加仓/减仓/盈亏/我的基金** → 需要结合持仓数据，选 risk_assessor 或 allocation_advisor\n\n"
                "## 输出要求\n"
                "只输出 JSON，不要其他文字。"
            ),
            "knowledge_scope": '{}',
            "icon": "robot",
            "is_preset": 1,
        },
    ]
    for agent in presets:
        conn.execute("""
            INSERT OR IGNORE INTO agents (name, description, system_prompt, knowledge_scope, icon, is_preset)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (agent["name"], agent["description"], agent["system_prompt"],
              agent["knowledge_scope"], agent["icon"], agent["is_preset"]))
        # 更新已存在的预设 Agent 的 system_prompt
        conn.execute("""
            UPDATE agents SET description=?, system_prompt=?, knowledge_scope=?, icon=?
            WHERE name=? AND is_preset=1
        """, (agent["description"], agent["system_prompt"], agent["knowledge_scope"],
              agent["icon"], agent["name"]))


# ── Agent CRUD ──────────────────────────────────────

def list_agents() -> list[dict]:
    """列出所有 Agent。"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM agents ORDER BY is_preset DESC, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent(agent_id: int) -> dict | None:
    """获取单个 Agent。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_agent(name: str, system_prompt: str, description: str = "",
                 knowledge_scope: str = "", icon: str = "robot") -> int:
    """创建自定义 Agent。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO agents (name, description, system_prompt, knowledge_scope, icon) VALUES (?, ?, ?, ?, ?)",
        (name, description, system_prompt, knowledge_scope, icon),
    )
    agent_id = cur.lastrowid
    conn.commit()
    conn.close()
    return agent_id


def update_agent(agent_id: int, **fields):
    """更新 Agent 字段。"""
    if not fields:
        return
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [agent_id]
    conn.execute(f"UPDATE agents SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_agent(agent_id: int):
    """删除 Agent（仅限非预设）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM agents WHERE id = ? AND is_preset = 0", (agent_id,))
    conn.commit()
    conn.close()


# ── 编排专家 Agent 加载（带缓存） ──────────────────────────────

import time as _time
import json as _json

_specialist_cache = None
_specialist_cache_ts = 0
_SPECIALIST_CACHE_TTL = 60  # 秒


def load_specialist_agents() -> dict:
    """从数据库加载所有编排专家 Agent，返回 {agent_key: {name, icon, description, tools, system_prompt}}。

    带 60 秒内存缓存，避免每次请求都查库。
    """
    global _specialist_cache, _specialist_cache_ts
    if _specialist_cache is not None and (_time.time() - _specialist_cache_ts) < _SPECIALIST_CACHE_TTL:
        return _specialist_cache

    conn = _get_conn()
    rows = conn.execute(
        "SELECT agent_key, name, icon, description, tools, system_prompt FROM agents WHERE is_specialist = 1"
    ).fetchall()
    conn.close()

    # 文字 icon → emoji 映射（与前端 getAgentIcon 保持一致）
    _icon_map = {
        "chart": "📊", "research": "🔍", "shield": "🛡️", "pie": "🥧",
        "robot": "🤖", "newspaper": "📰", "search": "🔍", "bull": "🐂",
    }

    result = {}
    for r in rows:
        tools = _json.loads(r["tools"]) if r["tools"] else []
        raw_icon = r["icon"] or "robot"
        emoji_icon = _icon_map.get(raw_icon, raw_icon)
        # 如果已经是 emoji（不在 map 中），直接使用
        result[r["agent_key"]] = {
            "name": r["name"],
            "icon": emoji_icon,
            "description": r["description"] or "",
            "tools": tools,
            "system_prompt": r["system_prompt"],
        }

    _specialist_cache = result
    _specialist_cache_ts = _time.time()
    return result


def clear_specialist_cache():
    """清除专家 Agent 缓存（更新 Agent 后调用）。"""
    global _specialist_cache, _specialist_cache_ts
    _specialist_cache = None
    _specialist_cache_ts = 0


# ── Agent 提示词版本 CRUD ──────────────────────────────


def save_prompt_version(agent_id: int, agent_type: str, system_prompt: str, conn=None):
    """保存当前提示词到版本历史（在更新 agent 前调用）。"""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(version) as max_ver FROM agent_prompt_versions WHERE agent_id = ? AND agent_type = ?",
        (agent_id, agent_type)
    ).fetchone()
    next_ver = (row["max_ver"] or 0) + 1
    conn.execute(
        "INSERT INTO agent_prompt_versions (agent_id, agent_type, system_prompt, version) VALUES (?, ?, ?, ?)",
        (agent_id, agent_type, system_prompt, next_ver)
    )
    if own_conn:
        conn.commit()
        conn.close()


def list_prompt_versions(agent_id: int, agent_type: str = 'conversation') -> list[dict]:
    """列出某 Agent 的所有提示词版本，最新在前。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM agent_prompt_versions WHERE agent_id = ? AND agent_type = ? ORDER BY version DESC",
        (agent_id, agent_type)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prompt_version(version_id: int) -> dict | None:
    """获取单个版本详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM agent_prompt_versions WHERE id = ?", (version_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Agent Runs ──────────────────────────────────────────


def create_agent_run(conversation_id: int, message_id: int, agent_key: str,
                     agent_name: str, query: str, result: str = "",
                     tool_calls: str = "", duration_ms: int = 0,
                     trace_id: str = "", status: str = "success") -> int:
    """记录一次专家 Agent 调用，返回 run_id。status: success / error / timeout。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO agent_runs (conversation_id, message_id, agent_key, agent_name,
                                query, result, tool_calls, duration_ms, trace_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (conversation_id, message_id, agent_key, agent_name,
          query, result, tool_calls, duration_ms, trace_id, status))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def create_pending_agent_run(conversation_id: int, message_id: int, agent_key: str,
                              agent_name: str, query: str = "", trace_id: str = "") -> int:
    """创建 pending 状态的 agent 执行记录，返回 run_id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO agent_runs (conversation_id, message_id, agent_key, agent_name,
                                query, trace_id, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (conversation_id, message_id, agent_key, agent_name, query, trace_id))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def update_agent_run_status(run_id: int, status: str, result: str = None,
                             duration_ms: int = None, error_message: str = None):
    """更新 agent 执行记录状态。status: running / completed / failed / cancelled。"""
    conn = _get_conn()
    updates = ["status = ?"]
    params = [status]

    if result is not None:
        updates.append("result = ?")
        params.append(result)
    if duration_ms is not None:
        updates.append("duration_ms = ?")
        params.append(duration_ms)
    if status == "completed":
        updates.append("completed_at = datetime('now','localtime')")
    elif status == "running":
        updates.append("started_at = datetime('now','localtime')")

    params.append(run_id)
    conn.execute(f"UPDATE agent_runs SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()


def get_completed_agents_for_message(message_id: int) -> list[dict]:
    """获取某条消息下已完成的 agent 执行记录。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT agent_key, agent_name, result, duration_ms, tool_calls
        FROM agent_runs
        WHERE message_id = ? AND status IN ('completed', 'success')
        ORDER BY id ASC
    """, (message_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cancel_running_agents(message_id: int):
    """取消某条消息下所有 running/pending 状态的 agent。"""
    conn = _get_conn()
    conn.execute("""
        UPDATE agent_runs SET status = 'cancelled'
        WHERE message_id = ? AND status IN ('pending', 'running')
    """, (message_id,))
    conn.commit()
    conn.close()


def get_agent_runs(conversation_id: int, limit: int = 50) -> list[dict]:
    """获取对话的专家 Agent 调用记录。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM agent_runs
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conversation_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_running_agent_count(conversation_id: int) -> int:
    """获取对话中正在运行的 agent 数量（用于防重复发送）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM agent_runs
        WHERE conversation_id = ? AND status IN ('pending', 'running')
    """, (conversation_id,)).fetchone()
    conn.close()
    return row["cnt"] if row else 0
