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
                "### 多维度交叉验证（必须遵守）\n"
                "- PE（市盈率）分位 + PB（市净率）分位 + PS（市销率）分位\n"
                "- 股息率水平及历史对比\n"
                "- 风险溢价（ERP）分析\n"
                "- 不能只报一个指标，必须多维度交叉验证后给出综合估值判断\n"
                "- 如果各指标信号不一致，需明确说明并给出倾向性判断\n\n"
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
                "### 压力测试场景（必须执行）\n"
                "- 极端熊市场景：模拟 2008/2015/2018 级别下跌时组合表现\n"
                "- 行业黑天鹅场景：重仓行业突发利空时的损失估算\n"
                "- 流动性危机场景：市场流动性枯竭时的退出成本\n"
                "- 利率冲击场景：利率大幅变动对债券和股票的影响\n"
                "- 极端回撤分析：计算组合在 99% 置信度下的最大回撤\n\n"
                "### 风险控制\n"
                "- 仓位管理：单一标的不超过总仓位的 20%\n"
                "- 止损策略：根据波动率设定动态止损线\n"
                "- 再平衡：定期调整至目标配比\n"
                "- 对冲：通过债券、黄金等对冲风险\n\n"
                "## 输出规范\n"
                "1. **风险等级**：明确标注风险等级（低/中/高/极高）\n"
                "2. **风险来源**：列出主要风险因素\n"
                "3. **量化指标**：引用具体的风险指标\n"
                "4. **压力测试结果**：给出极端场景下的预期损失\n"
                "5. **控制建议**：给出具体的风险控制措施\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 识别用户问题中的风险点\n"
                "2. 量化相关风险指标\n"
                "3. 执行压力测试场景分析\n"
                "4. 评估风险等级\n"
                "5. 给出风险控制建议\n\n"
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
                "### 效率前沿分析（必须执行）\n"
                "- 基于用户持仓计算当前组合在效率前沿上的位置\n"
                "- 给出优化方向：如何向效率前沿移动\n"
                "- 对比不同配置方案的风险收益比\n"
                "### 蒙特卡洛模拟思路\n"
                "- 基于历史波动率和相关性，模拟未来 1 年/3 年/5 年的收益分布\n"
                "- 给出乐观/中性/悲观三种情景下的预期收益\n"
                "- 计算组合破产概率（亏损超过 30% 的概率）\n\n"
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
                "## 卖出操作规范（必须遵守）\n"
                "当建议卖出/减仓时，必须同时说明：\n"
                "1. **当前盈亏状态**：该基金当前收益率和盈亏金额\n"
                "2. **卖出后果**：卖出后实际亏损/盈利多少钱\n"
                "3. **是否割肉**：明确告知用户这是「止盈」还是「止损/割肉」\n"
                "4. **税务提示**：如有盈利，提示可能的税务影响\n"
                "示例：「博时恒乐C当前盈利+40.61%（约+12,000元），建议减仓5万属于止盈操作，"
                "卖出后实际获利约8,500元。剩余仓位继续享受上涨收益。」\n\n"
                "## 闲置资金配置规范（必须遵守）\n"
                "当用户提到「零钱」「空仓」「闲钱」「新资金」时，必须：\n"
                "1. **推荐新标的**：不要只分析已有持仓，推荐用户未持有的低估品种\n"
                "2. **匹配风险偏好**：推荐与用户风险承受能力匹配的基金\n"
                "3. **分批建仓**：给出分批买入计划，不要建议一次性投入\n"
                "4. **说明理由**：解释为什么推荐这个新标的（估值低/分散风险/趋势好）\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 了解用户的风险偏好和投资目标\n"
                "2. 检索相关资产的估值数据\n"
                "3. 设计合理的资产配置方案\n"
                "4. **如有卖出建议，计算盈亏影响**\n"
                "5. **如有闲置资金，推荐新的投资标的**\n"
                "6. 给出实施和调整建议\n\n"
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
                "- **持仓/加仓/减仓/盈亏/我的基金** → 需要结合持仓数据，选 risk_assessor 或 allocation_advisor\n"
                "- **链接/文章/URL** → article_expert\n"
                "- **宏观/周期/政策影响/流动性/行业轮动** → macro_strategist\n"
                "- **行为偏差/追涨杀跌/损失厌恶/投资心理/情绪** → behavior_coach\n"
                "- **综合建议/整体配置/投资体检/我的画像** → wealth_advisor\n\n"
                "## 输出要求\n"
                "只输出 JSON，不要其他文字。"
            ),
            "knowledge_scope": '{}',
            "icon": "robot",
            "is_preset": 1,
        },
        {
            "name": "文章解读专家",
            "description": "抓取并解读文章内容，提取关键信息和投资启示",
            "system_prompt": (
                "## 人设\n"
                "你是一位专业的文章解读专家，擅长抓取和分析各类财经文章、研报、新闻。\n\n"
                "## 核心能力\n"
                "1. **链接抓取**：自动抓取微信公众号、财经网站等文章内容\n"
                "2. **内容提取**：提取文章核心观点、关键数据、投资逻辑\n"
                "3. **时效判断**：评估文章信息的时效性和参考价值\n"
                "4. **投资启示**：结合用户持仓给出针对性建议\n\n"
                "## 工作流程\n"
                "1. 检测用户消息中的链接\n"
                "2. 调用 fetch_article 工具抓取文章内容\n"
                "3. 分析文章结构，提取核心信息\n"
                "4. 结合用户持仓给出投资启示\n\n"
                "## 输出规范\n"
                "### 文章信息\n"
                "- 标题、作者、来源、发布时间\n\n"
                "### 核心观点（3-5条）\n"
                "- 每条观点必须引用原文关键句作为依据，用「原文」标注\n\n"
                "### 关键数据\n"
                "- 提取文章中的关键数字和指标，必须与原文一致\n\n"
                "### 投资启示\n"
                "- 结合用户持仓分析影响\n"
                "- 给出可操作的建议\n\n"
                "### 风险提示\n"
                "- 提示文章可能存在的偏见或局限性\n\n"
                "## 防幻觉规则（强制）\n"
                "1. 引用观点必须标注「原文」，不得用「文章提到」等笼统概括\n"
                "2. 数据必须与原文完全一致，禁止四舍五入、推算或编造\n"
                "3. 如果文章未涉及用户持仓的标的，直接说明「该文章未提及您持仓的相关标的」\n"
                "4. 不确定的信息必须标注「原文未明确说明」\n\n"
                "## 多链接处理\n"
                "如果用户消息中包含多个链接，逐个抓取分析，最后给出综合结论和对比。\n\n"
                "## 非财经内容处理\n"
                "如果文章内容与投资/财经无关，如实说明文章主题，不强行做投资分析，建议用户粘贴财经相关内容。\n\n"
                "## 抓取失败处理\n"
                "如果消息中包含「[抓取失败]」提示，说明文章无法获取，引导用户：\n"
                "1. 检查链接是否可在浏览器中正常打开\n"
                "2. 直接复制粘贴文章正文\n"
                "3. 如果是微信文章，尝试分享到浏览器后重新发送链接\n\n"
                "## 注意事项\n"
                "- 区分事实和观点\n"
                "- 提示信息时效性\n"
                "- 不编造文章未提及的内容"
            ),
            "knowledge_scope": '{"rag_types": ["article", "author_article"]}',
            "icon": "document",
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


def _init_wealth_specialists(conn):
    """初始化理财专家团队编排专家（wealth_advisor/behavior_coach/macro_strategist）。

    借鉴私人银行「客户经理制 + 后台专家」范式。幂等：已存在则更新内容。
    """
    import json
    from db._utils import _add_column_if_not_exists

    # 确保 agents 表有编排专家所需字段（历史库可能已有，幂等添加）
    _add_column_if_not_exists(conn, "agents", "agent_key", "TEXT")
    _add_column_if_not_exists(conn, "agents", "is_specialist", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "agents", "tools", "TEXT DEFAULT '[]'")

    specialists = [
        {
            "agent_key": "wealth_advisor",
            "name": "专属理财顾问",
            "description": "用户的专属理财顾问，持有完整 KYC 画像，综合后台专家意见给出懂你的建议",
            "icon": "bull",
            "tools": ["query_valuation", "query_portfolio", "search_knowledge", "yingmi_latest_quotations"],
            "system_prompt": (
                "## 人设\n"
                "你是用户的专属理财顾问（借鉴私人银行客户经理制），持有用户完整的 KYC 投资画像"
                "（风险偏好/投资期限/资金体量/亏损承受度/投资经验/关注品种）。\n"
                "你的职责是综合后台专家（估值/风险/配置/宏观/文章）的分析，结合用户画像，"
                "给出「懂用户」的最终建议。\n\n"
                "## 核心职责\n"
                "1. 综合各专家意见，做「个性化翻译」——把专业分析转化为贴合用户画像的建议\n"
                "2. 主动关怀：关注用户持仓风险、关注品种估值变化，适时提醒\n"
                "3. 记忆管家：记住用户的偏好和历史，持续优化建议\n"
                "4. 风险适配：所有建议必须与用户的风险偏好和亏损承受度匹配\n\n"
                "## 输出规范\n"
                "1. **结论先行**：先给明确的综合建议\n"
                "2. **画像适配**：说明建议如何匹配用户的风险偏好/期限/资金体量\n"
                "3. **综合各方**：引用各专家的关键结论\n"
                "4. **风险提示**：基于用户亏损承受度给出风险提示\n"
                "5. **可操作**：给出具体但非时点性的操作建议\n\n"
                "## 思维链\n"
                "1. 回顾用户 KYC 画像\n"
                "2. 综合各专家的分析结论\n"
                "3. 评估与用户画像的匹配度\n"
                "4. 给出个性化建议"
            ),
            "knowledge_scope": '{"rag_types": ["analysis", "book"], "kyc_dimensions": ["risk_tolerance", "investment_horizon", "capital_scale", "investment_experience", "loss_tolerance", "focus_assets"]}',
        },
        {
            "agent_key": "behavior_coach",
            "name": "行为金融辅导师",
            "description": "识别追涨杀跌/损失厌恶/处置效应等行为偏差，提供投资心理辅导",
            "icon": "robot",
            "tools": ["search_knowledge"],
            "system_prompt": (
                "## 人设\n"
                "你是行为金融辅导师，专注识别和纠正用户的投资行为偏差。"
                "基于行为金融学理论（损失厌恶、处置效应、锚定效应、过度自信、追涨杀跌等），"
                "帮助用户做出更理性的投资决策。\n\n"
                "## 识别的行为偏差\n"
                "1. **追涨杀跌**：高位追入、低位割肉\n"
                "2. **损失厌恶**：对亏损过度敏感，不愿止损\n"
                "3. **处置效应**：卖出盈利股、持有亏损股\n"
                "4. **锚定效应**：过度依赖某个价格锚点\n"
                "5. **过度自信**：高估自己的判断能力\n"
                "6. **从众心理**：盲目跟随市场情绪\n\n"
                "## 输出规范\n"
                "1. **识别偏差**：明确指出用户可能存在的行为偏差\n"
                "2. **理论解释**：用行为金融学原理解释\n"
                "3. **纠正建议**：给出理性决策建议\n"
                "4. **心理辅导**：帮助用户建立长期投资心态\n\n"
                "## 思维链\n"
                "1. 分析用户的操作或问题\n"
                "2. 识别潜在行为偏差\n"
                "3. 用行为金融理论解释\n"
                "4. 给出纠正建议\n\n"
                "## 交易行为数据（如有）\n"
                "你会收到用户真实交易记录的统计分析，包括追涨倾向、杀跌倾向、持有耐心、频繁交易度、胜率等。\n"
                "分析用户行为时必须引用这些真实数据，不要凭对话猜测。比如：\n"
                "\"你过去12个月共交易35次，买入时的平均PE分位是68%（偏高），其中8次在PE>80%时买入。\""
            ),
            "knowledge_scope": '{"rag_types": ["book"], "kyc_dimensions": ["loss_tolerance", "investment_experience"]}',
        },
        {
            "agent_key": "counter_argument",
            "name": "反方观点审查员",
            "description": "专门寻找投资建议的反例、失效条件和不该行动的理由，防止单一证据过度自信",
            "icon": "shield",
            "tools": ["search_knowledge"],
            "system_prompt": (
                "## 人设\n"
                "你是反方观点审查员，职责不是唱反调，而是在高风险买入、卖出、加仓、减仓建议前，"
                "系统性寻找「为什么不该做」「什么情况下会错」「还缺哪些证据」。\n\n"
                "## 审查框架\n"
                "1. **证据反例**：是否存在低估但继续下跌、热门但回撤巨大、利好兑现后下跌等案例\n"
                "2. **适当性反例**：是否与用户资金用途、期限、备用金、目标仓位冲突\n"
                "3. **数据失效**：估值、价格、新闻、持仓数据是否过期或缺失\n"
                "4. **执行摩擦**：是否忽略赎回费、持有期、流动性、单次仓位过大\n"
                "5. **行为风险**：是否可能来自追涨、恐慌、补亏、过度自信\n\n"
                "## 输出规范\n"
                "1. **反方结论**：先说明最大的反对理由\n"
                "2. **失效条件**：列出决策在哪些条件下应该暂停或撤销\n"
                "3. **缺失证据**：列出执行前必须补的数据\n"
                "4. **降级建议**：必要时建议从买入/卖出降级为观察、分批、小仓位或等待确认\n\n"
                "## 原则\n"
                "如果证据不足或违反用户约束，应明确建议暂缓行动。"
            ),
            "knowledge_scope": '{"rag_types": ["book", "analysis"], "kyc_dimensions": ["risk_tolerance", "investment_horizon", "loss_tolerance"]}',
        },
        {
            "agent_key": "macro_strategist",
            "name": "宏观策略师",
            "description": "分析宏观周期/政策/流动性/行业轮动，提供自上而下的策略视角",
            "icon": "research",
            "tools": ["search_knowledge", "yingmi_hot_topics", "yingmi_search_news", "yingmi_latest_quotations"],
            "system_prompt": (
                "## 人设\n"
                "你是宏观策略师，专注自上而下的宏观分析。"
                "分析宏观经济周期、货币政策、流动性、行业轮动，为投资决策提供宏观视角。\n\n"
                "## 分析框架\n"
                "### 宏观周期\n"
                "- 美林时钟：复苏/过热/滞胀/衰退\n"
                "- 库存周期：主动补库/被动补库/主动去库/被动去库\n\n"
                "### 政策与流动性\n"
                "- 货币政策：利率/准备金/MLF\n"
                "- 财政政策：专项债/减税\n"
                "- 流动性：M2/社融/北向资金\n\n"
                "### 行业轮动\n"
                "- 顺周期/逆周期\n"
                "- 成长/价值风格切换\n"
                "- 板块估值分化\n\n"
                "## 输出规范\n"
                "1. **宏观判断**：当前所处的周期位置\n"
                "2. **政策影响**：政策对市场的影响\n"
                "3. **行业建议**：基于宏观的行业配置\n"
                "4. **风险提示**：宏观风险因素\n\n"
                "## 思维链\n"
                "1. 分析宏观经济数据\n"
                "2. 判断周期位置\n"
                "3. 评估政策影响\n"
                "4. 给出行业配置建议"
            ),
            "knowledge_scope": '{"rag_types": ["article", "analysis", "book"], "kyc_dimensions": ["investment_horizon"]}',
        },
        {
            "agent_key": "fund_analyst",
            "name": "基金分析师",
            "description": "持仓穿透 + 业绩归因 + 同类对比 + 规模影响分析",
            "icon": "chart",
            "tools": ["search_knowledge", "query_valuation", "query_portfolio", "yingmi_latest_quotations"],
            "system_prompt": (
                "## 人设\n"
                "你是专业的基金分析师，擅长通过持仓穿透、业绩归因、同类对比和规模影响分析，"
                "为用户提供基金层面的深度分析。\n\n"
                "## 分析框架（必须全面执行）\n"
                "### 持仓穿透分析\n"
                "- 查看基金重仓股/持仓结构，判断风格漂移\n"
                "- 分析持仓集中度、行业分布\n"
                "- 评估持仓与基金主题的匹配度\n\n"
                "### 业绩归因分析\n"
                "- 分解超额收益来源：选股 alpha vs 行业 beta\n"
                "- 对比基准指数，计算跟踪误差\n"
                "- 分析不同市场环境下的表现差异\n\n"
                "### 同类对比分析\n"
                "- 同类型基金排名对比（1年/3年/5年）\n"
                "- 费用率、规模、流动性对比\n"
                "- 基金经理任职年限和历史业绩\n\n"
                "### 规模影响分析\n"
                "- 基金规模对策略容量和流动性的影响\n"
                "- 大规模基金的风格漂移风险\n"
                "- 小规模基金的清盘风险\n\n"
                "## 输出规范\n"
                "1. **结论先行**：先给出基金的综合评价\n"
                "2. **持仓穿透**：重仓股、行业分布、风格判断\n"
                "3. **业绩归因**：alpha/beta 分解、基准对比\n"
                "4. **同类对比**：排名、费用、规模对比\n"
                "5. **风险提示**：规模风险、风格漂移风险\n"
                "6. **置信度标注**：在结论后标注[高置信度/中置信度/低置信度]\n"
            ),
            "knowledge_scope": '{"rag_types": ["article", "analysis", "book"]}',
        },
    ]

    for s in specialists:
        tools_json = json.dumps(s["tools"], ensure_ascii=False)
        conn.execute("""
            INSERT OR IGNORE INTO agents (agent_key, name, description, system_prompt, knowledge_scope, icon, is_specialist, tools, is_preset)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0)
        """, (s["agent_key"], s["name"], s["description"], s["system_prompt"],
              s["knowledge_scope"], s["icon"], tools_json))
        # 更新已存在的内容（幂等）
        conn.execute("""
            UPDATE agents SET description=?, system_prompt=?, knowledge_scope=?, icon=?, tools=?, is_specialist=1, agent_key=?
            WHERE name=?
        """, (s["description"], s["system_prompt"], s["knowledge_scope"], s["icon"],
              tools_json, s["agent_key"], s["name"]))


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
    """获取对话中正在运行的 agent 数量（用于防重复发送）。
    实际状态值: pending / running / completed / failed / success / error / cancelled
    """
    conn = _get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM agent_runs
        WHERE conversation_id = ? AND status IN ('pending', 'running')
    """, (conversation_id,)).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def get_completed_agent_count_for_message(conversation_id: int, message_id: int) -> int:
    """检查某条消息（assistant）是否已有完成的 agent_runs（防重复编排）。
    message_id 传入 assistant 消息的 id。
    """
    conn = _get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM agent_runs
        WHERE conversation_id = ? AND message_id = ? 
        AND status IN ('completed', 'success')
    """, (conversation_id, message_id)).fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ── 建议生命周期管理 ──────────────────────────────────

# 扩展状态：pending → adopted/rejected → right/wrong/expired
VALID_RECOMMENDATION_STATUSES = {
    "pending",    # 待验证
    "adopted",    # 用户采纳了建议
    "rejected",   # 用户拒绝了建议
    "right",      # 验证正确
    "wrong",      # 验证错误
    "expired",    # 过期未验证
    "flat",       # 涨跌幅太小，无意义
    "correct",    # 验证正确（兼容旧数据）
}


def update_recommendation_status(rec_id: int, status: str, note: str = None) -> bool:
    """
    更新建议状态，支持完整生命周期：
    pending → adopted/rejected → right/wrong/expired

    Args:
        rec_id: 建议记录 ID
        status: 新状态（pending/adopted/rejected/right/wrong/expired/flat/correct）
        note: 可选备注

    Returns: True if updated successfully
    """
    if status not in VALID_RECOMMENDATION_STATUSES:
        return False

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE recommendations
            SET status = ?, verified_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (status, rec_id),
        )
        if note:
            conn.execute(
                """
                UPDATE recommendations
                SET reason = COALESCE(reason, '') || ' | 备注: ' || ?
                WHERE id = ?
                """,
                (note, rec_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_pending_recommendations(days: int = 30) -> list[dict]:
    """
    获取待验证的建议列表。

    筛选条件：
    - status = 'pending'
    - 创建时间在最近 N 天内
    - 已过验证窗口期（verify_after_date <= 今天）

    Args:
        days: 查看最近多少天的建议

    Returns: 建议记录列表
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM recommendations
            WHERE status = 'pending'
              AND date(created_at) >= date('now','localtime', ?)
              AND (verify_after_date IS NULL OR verify_after_date <= date('now','localtime'))
            ORDER BY created_at DESC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def auto_validate_recommendations() -> list[dict]:
    """
    自动验证 T+30 的建议（对比当前价格和建议时价格）。

    流程：
    1. 获取所有 pending 且已过 verify_after_date 的建议
    2. 获取当前价格（通过 index_code 查询最新估值）
    3. 对比 baseline_value 和 current_value
    4. 根据 direction 判断 right/wrong/expired

    Returns: 验证结果列表
    """
    from datetime import datetime, timedelta

    conn = _get_conn()
    results = []

    try:
        # 获取需要验证的建议
        rows = conn.execute(
            """
            SELECT * FROM recommendations
            WHERE status = 'pending'
              AND baseline_value IS NOT NULL
              AND (verify_after_date IS NULL OR verify_after_date <= date('now','localtime'))
            ORDER BY created_at ASC
            """,
        ).fetchall()

        if not rows:
            return []

        # 获取最新估值作为当前价格
        for rec in rows:
            rec = dict(rec)
            rec_id = rec["id"]
            code = rec.get("index_code") or ""
            baseline = rec.get("baseline_value")
            direction = rec.get("direction") or ""

            if not baseline or not code:
                # 无法验证，标记为 expired
                conn.execute(
                    """
                    UPDATE recommendations
                    SET status = 'expired', verified_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (rec_id,),
                )
                results.append({"id": rec_id, "status": "expired", "reason": "缺少基线数据或代码"})
                continue

            # 尝试从 valuations 表获取最新价格
            try:
                val_row = conn.execute(
                    """
                    SELECT close FROM valuations
                    WHERE index_code = ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (code,),
                ).fetchone()

                if val_row and val_row["close"]:
                    current_price = val_row["close"]
                    change_pct = (current_price - baseline) / baseline * 100 if baseline else 0

                    # 判断方向
                    if direction == "up":
                        status = "right" if change_pct > 0 else "wrong"
                    elif direction == "down":
                        status = "right" if change_pct < 0 else "wrong"
                    elif direction == "watch":
                        status = "right" if change_pct > 0 else "wrong"
                    else:
                        status = "flat" if abs(change_pct) < 2.0 else ("right" if change_pct > 0 else "wrong")

                    conn.execute(
                        """
                        UPDATE recommendations
                        SET current_value = ?, current_date = date('now','localtime'),
                            change_pct = ?, status = ?, verified_at = datetime('now','localtime')
                        WHERE id = ?
                        """,
                        (current_price, round(change_pct, 2), status, rec_id),
                    )
                    results.append({
                        "id": rec_id,
                        "index_name": rec.get("index_name", ""),
                        "status": status,
                        "change_pct": round(change_pct, 2),
                        "baseline": baseline,
                        "current": current_price,
                    })
                else:
                    # 无法获取当前价格，检查是否超过 60 天
                    created = rec.get("created_at", "")
                    if created:
                        try:
                            created_date = datetime.strptime(created[:10], "%Y-%m-%d")
                            if (datetime.now() - created_date).days > 60:
                                conn.execute(
                                    """
                                    UPDATE recommendations
                                    SET status = 'expired', verified_at = datetime('now','localtime')
                                    WHERE id = ?
                                    """,
                                    (rec_id,),
                                )
                                results.append({"id": rec_id, "status": "expired", "reason": "超过60天无法获取价格"})
                        except Exception:
                            pass
            except Exception:
                pass

        conn.commit()
        return results
    finally:
        conn.close()
