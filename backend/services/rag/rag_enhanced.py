"""RAG 检索增强模块。

提供查询扩展、多路召回、轻量级重排序等功能。
"""

import re
from typing import List, Dict, Tuple, Optional

# ══════════════════════════════════════════════════════════════
# 同义词词典
# ══════════════════════════════════════════════════════════════

SYNONYMS = {
    # 估值指标
    "PE": ["市盈率", "估值", "valuation", "earnings ratio", "盈利收益率"],
    "PB": ["市净率", "净资产", "book value", "账面价值"],
    "PEG": ["成长性估值", "增长比率"],
    "PS": ["市销率", "营收比"],
    "股息": ["分红", "dividend", "派息", "红利"],
    "估值": ["PE", "PB", "市盈率", "市净率", "百分位", "分位点"],

    # 投资策略
    "定投": ["定期定额", "DCA", "dollar cost averaging", "定额投资", "定额买入"],
    "止盈": ["获利了结", "take profit", "profit taking", "卖出", "落袋为安"],
    "止损": ["stop loss", "割肉", "清仓止损", "认赔出局"],
    "回撤": ["下跌", "drawdown", "decline", "跌幅", "回调"],
    "抄底": ["低吸", "逢低买入", "buy the dip", "底部买入"],
    "追涨": ["追高", "追买"],
    "买入": ["建仓", "入场", "加仓", "申购", "认购", "进场", "买"],
    "卖出": ["清仓", "离场", "抛售", "出货", "赎回", "减持", "卖"],
    "持有": ["持仓", "持股", "拿着", "长期持有"],
    "配置": ["配置比例", "资产配比", "仓位", "组合"],
    "分散": ["多元化", "分仓", "分散投资", "分批", "分散风险"],

    # 口语化表达 → 专业术语
    "什么时候卖": ["卖出时机", "离场信号", "卖出规则", "获利了结"],
    "什么时候买": ["买入时机", "入场信号", "买入规则", "建仓时机"],
    "怎么看": ["如何判断", "如何分析", "如何评估"],
    "怎么选": ["如何选择", "如何筛选", "挑选标准", "选择标准"],
    "怎么办": ["应对策略", "操作建议", "应对方法"],
    "太高": ["偏高", "过高", "高估"],
    "太低": ["偏低", "过低", "低估"],

    # 风险指标
    "夏普": ["夏普比率", "sharpe ratio", "风险调整收益"],
    "波动": ["波动率", "volatility", "标准差"],
    "风险": ["risk", "不确定性", "回撤", "亏损"],
    "恐慌": ["恐惧", "悲观", "抛售潮", "踩踏", "市场恐慌", "情绪崩溃"],
    "贪婪": ["乐观", "狂热", "追高", "泡沫"],

    # 基金类型
    "指数基金": ["ETF", "被动基金", "index fund", "指数增强"],
    "主动基金": ["主动管理基金", "active fund"],
    "债券基金": ["债基", "bond fund", "纯债基金"],
    "货币基金": ["余额宝", "money market fund"],
    "费率": ["管理费", "托管费", "申购费", "赎回费", "手续费", "佣金", "成本"],

    # 宏观指标
    "GDP": ["国内生产总值", "经济增长"],
    "CPI": ["通胀", "物价", "消费价格指数"],
    "PMI": ["采购经理指数", "经济景气"],
    "M2": ["货币供应", "流动性"],

    # 市场术语
    "牛市": ["上涨", "多头", "bull market", "行情好", "上涨趋势"],
    "熊市": ["下跌", "空头", "bear market", "行情差", "下跌趋势"],
    "震荡": ["横盘", "盘整", "区间波动"],
    "趋势": ["方向", "走势", "trend"],
    "判断": ["辨别", "识别", "区分", "分析"],

    # 人物 / 书籍关联
    "格雷厄姆": ["本杰明·格雷厄姆", "价值投资之父", "聪明的投资者"],
    "巴菲特": ["沃伦·巴菲特", "股神", "伯克希尔"],
    "彼得林奇": ["彼得·林奇", "林奇", "麦哲伦基金"],
    "霍华德马克斯": ["霍华德·马克斯", "橡树资本", "投资最重要的事"],
    "安全边际": ["margin of safety", "错误边际", "边际安全"],
    "资产配置": ["asset allocation", "配置比例", "股债配比", "组合配置"],
    "久期": ["duration", "久期缺口", "利率风险"],
    "红利再投资": ["分红再投资", "reinvest", "股息再投入"],
    "鸡尾酒会": ["鸡尾酒会理论", "林奇鸡尾酒会", "市场情绪指标"],

    # 跨资产：美股
    "美股": ["S&P 500", "NASDAQ", "NYSE", "美国股票", "标普500", "道琼斯", "Dow Jones", "US equity"],
    "纳斯达克": ["NASDAQ", "纳指", "QQQ", "科技股指"],
    "标普500": ["S&P 500", "SPX", "SPY", "美国大盘"],
    "美联储": ["Fed", "Federal Reserve", "FOMC", "鲍威尔", "联储", "联邦基金利率", "fed funds rate"],
    "加息": ["rate hike", "紧缩", "tightening", "提高利率", "升息"],
    "降息": ["rate cut", "宽松", "easing", "降低利率", "降准"],
    "美股估值": ["S&P 500 PE", "Shiller PE", "CAPE", "美股市盈率"],
    "EPS": ["每股收益", "盈利增长", "earnings per share"],

    # 跨资产：黄金
    "黄金": ["gold", "XAU", "XAU/USD", "贵金属", "COMEX黄金", "避险资产", "抗通胀"],
    "实际利率": ["real interest rate", "名义利率-通胀", "TIPS yield", "真实利率"],
    "美元": ["USD", "美元指数", "DXY", "汇率", "美金"],
    "央行购金": ["gold purchase", "储备黄金", "官方黄金储备"],

    # 跨资产：债券
    "美债": ["美国国债", "Treasury", "US Treasury", "美债收益率", "收益率曲线", "期限利差"],
    "债券": ["bond", "固收", "fixed income", "国债", "利率债", "信用债"],
    "收益率曲线": ["yield curve", "term structure", "期限结构", "利率期限结构"],
    "倒挂": ["inversion", "inverted yield curve", "衰退信号", "收益率倒挂"],
    "信用利差": ["credit spread", "信用债-国债利差", "违约利差"],
    "中美利差": ["中美国债利差", "中国国债-美债利差", "利差倒挂"],

    # 跨资产：宏观
    "通胀": ["CPI", "PPI", "物价", "通货膨胀", "inflation", "deflation", "通缩"],
    "非农就业": ["NFP", "nonfarm payrolls", "美国就业", "失业率"],
    "央行政策": ["monetary policy", "货币政策", "量化宽松", "QE", "缩表"],

    # A股主要指数
    "沪深300": ["000300", "CSI300", "沪深三百", "300指数"],
    "中证500": ["000905", "CSI500", "中证五百"],
    "中证1000": ["000852", "CSI1000"],
    "创业板": ["399006", "创业板指", "创业板指数", "ChiNext"],
    "科创50": ["000688", "科创板50", "STAR50"],
    "上证50": ["000016", "SSE50"],
    "国证2000": ["399303"],
    "中证全指": ["H30184"],
    "中证红利": ["000922", "红利指数"],

    # 行业板块
    "消费": ["消费行业", "大消费", "必需消费", "可选消费", "食品饮料", "白酒", "家电", "旅游", "医药", "消费股", "消费板块", "消费指数"],
    "白酒": ["白酒指数", "中证白酒", "白酒股", "茅台", "五粮液", "泸州老窖"],
    "医药": ["医药行业", "医药指数", "医疗", "制药", "生物医药", "中药", "医疗器械"],
    "科技": ["科技行业", "科技股", "信息技术", "半导体", "芯片", "人工智能", "AI"],
    "新能源": ["新能源行业", "光伏", "锂电池", "电动车", "风电", "储能"],
    "金融": ["金融行业", "银行", "保险", "券商", "证券", "金融股"],
    "地产": ["房地产", "地产股", "万科", "保利", "碧桂园"],
    "军工": ["军工行业", "国防军工", "航天", "兵器"],
    "周期": ["周期股", "钢铁", "煤炭", "有色金属", "化工", "建材"],

    # 制造业/基建
    "高端装备": ["高端制造", "装备制造", "中证高端装备", "中证高端制造", "先进制造", "智能制造"],
    "制造": ["制造业", "高端制造", "中国制造", "工业制造"],
    "基建": ["基建工程", "中证基建", "基建指数", "基础设施", "新基建", "老基建", "工程建设"],
    "工程": ["工程建设", "工程机械", "基建工程"],
    "机械": ["机械设备", "工程机械", "工业机械"],

    # 环保/碳中和
    "环保": ["环保行业", "环境保护", "绿色环保", "碳中和", "低碳"],
    "碳中和": ["碳达峰", "碳排放", "绿色能源", "新能源"],
    "液冷": ["液冷技术", "冷却", "散热", "数据中心"],
    "水资源": ["水处理", "水务", "水利", "净化"],

    # AI/科技细分
    "AI": ["人工智能", "Artificial Intelligence", "大模型", "算力", "芯片", "GPU"],
    "人工智能": ["AI", "机器学习", "深度学习", "大模型", "智能"],
    "机器人": ["机器人指数", "机器人ETF", "工业机器人", "服务机器人"],
    "芯片": ["半导体", "集成电路", "IC", "晶圆"],

    # 港股指数
    "恒生科技": ["HSTECH", "恒生科技指数", "港股科技", "恒生科技指数"],
    "恒生指数": ["HSI", "恒指", "香港恒生"],
    "恒生消费": ["恒生消费指数"],
    "恒生医疗": ["恒生医疗保健"],

    # 美股指数
    "标普500": ["S&P 500", "SPX", "SPY", "美国大盘"],
    "纳斯达克": ["NASDAQ", "纳指", "QQQ", "科技股指"],
    "道琼斯": ["Dow Jones", "道指", "DJIA"],
}


# ══════════════════════════════════════════════════════════════
# 知识图谱（简化版）
# ══════════════════════════════════════════════════════════════

KNOWLEDGE_GRAPH = {
    "PE": {
        "related": ["PB", "PEG", "股息率", "百分位", "估值"],
        "parent": "估值指标",
        "description": "市盈率 = 股价 / 每股收益",
    },
    "PB": {
        "related": ["PE", "净资产", "破净"],
        "parent": "估值指标",
        "description": "市净率 = 股价 / 每股净资产",
    },
    "定投": {
        "related": ["指数基金", "定期定额", "微笑曲线", "估值定投", "红利再投资"],
        "parent": "投资策略",
        "description": "定期定额投资",
    },
    "指数基金": {
        "related": ["ETF", "定投", "被动投资", "沪深300", "估值", "费率"],
        "parent": "基金类型",
        "description": "跟踪指数的被动基金",
    },
    "估值": {
        "related": ["PE", "PB", "百分位", "低估", "高估", "买入", "卖出"],
        "parent": "投资分析",
        "description": "判断资产贵贱的方法",
    },
    "卖出": {
        "related": ["止盈", "止损", "获利了结", "清仓", "减仓"],
        "parent": "交易策略",
        "description": "退出持仓的操作",
    },
    "分散": {
        "related": ["资产配置", "风险控制", "组合", "多元化", "不相关资产"],
        "parent": "风险管理",
        "description": "通过多元化降低非系统性风险",
    },
    "恐慌": {
        "related": ["市场情绪", "恐惧贪婪指数", "逆向投资", "行为金融"],
        "parent": "市场心理",
        "description": "市场极端悲观情绪",
    },
    "安全边际": {
        "related": ["格雷厄姆", "价值投资", "低估买入", "错误边际"],
        "parent": "投资原则",
        "description": "以低于内在价值的价格买入",
    },
    "资产配置": {
        "related": ["股债配比", "再平衡", "分散投资", "核心卫星"],
        "parent": "投资策略",
        "description": "在不同资产间分配资金",
    },
    "久期": {
        "related": ["债券", "利率风险", "久期缺口", "收益率"],
        "parent": "债券分析",
        "description": "债券价格对利率变化的敏感度",
    },
    "鸡尾酒会": {
        "related": ["彼得林奇", "市场情绪", "逆向指标", "投资心理学"],
        "parent": "投资心理学",
        "description": "林奇用鸡尾酒会热度判断市场阶段",
    },
    # 跨资产节点
    "黄金": {
        "related": ["实际利率", "美元", "通胀", "CPI", "避险", "央行购金", "美元指数"],
        "parent": "大宗商品",
        "description": "黄金价格受实际利率、美元、避险需求驱动",
    },
    "实际利率": {
        "related": ["黄金", "美元", "通胀", "CPI", "TIPS", "名义利率"],
        "parent": "宏观指标",
        "description": "名义利率减去通货膨胀率，黄金定价核心变量",
    },
    "美联储": {
        "related": ["加息", "降息", "美元", "美债收益率", "美股", "黄金", "北向资金", "A股外资"],
        "parent": "宏观政策",
        "description": "美国联邦储备系统，全球流动性总阀门",
    },
    "美债收益率": {
        "related": ["收益率曲线", "倒挂", "美联储", "美元", "全球流动性", "中美利差", "A股估值"],
        "parent": "债券市场",
        "description": "美国国债到期收益率，全球资产定价锚",
    },
    "中美利差": {
        "related": ["美债收益率", "中国国债收益率", "人民币汇率", "北向资金", "A股"],
        "parent": "跨境资本流动",
        "description": "中国国债收益率与美国国债收益率之差",
    },
    "美股": {
        "related": ["S&P 500", "纳斯达克", "美联储", "EPS", "美股估值", "科技股"],
        "parent": "权益市场",
        "description": "美国股票市场",
    },
    "收益率曲线": {
        "related": ["美债收益率", "倒挂", "衰退", "久期", "期限利差"],
        "parent": "债券市场",
        "description": "不同期限债券收益率连成的曲线",
    },
    "美元": {
        "related": ["美联储", "黄金", "美债收益率", "汇率", "人民币", "全球流动性"],
        "parent": "外汇市场",
        "description": "美元指数，全球储备货币",
    },
    "通胀": {
        "related": ["CPI", "PPI", "实际利率", "黄金", "美联储", "央行政策", "物价"],
        "parent": "宏观指标",
        "description": "物价总水平持续上涨，影响资产配置和货币政策",
    },
    "美债": {
        "related": ["美债收益率", "美联储", "美元", "收益率曲线", "久期", "全球流动性"],
        "parent": "债券市场",
        "description": "美国国债，全球无风险资产基准",
    },

    # A股指数
    "沪深300": {
        "related": ["PE", "PB", "百分位", "估值", "大盘蓝筹", "A股"],
        "parent": "A股指数",
        "description": "沪深两市最大的300只股票，A股核心基准",
    },
    "创业板": {
        "related": ["成长股", "科技", "新能源", "医药", "估值"],
        "parent": "A股指数",
        "description": "创业板指数，代表成长型中小企业",
    },
    "科创50": {
        "related": ["科创板", "半导体", "芯片", "硬科技", "估值"],
        "parent": "A股指数",
        "description": "科创板50只核心股票，硬科技代表",
    },

    # 港股指数
    "恒生科技": {
        "related": ["港股", "科技股", "互联网", "腾讯", "阿里巴巴", "估值", "市销率"],
        "parent": "港股指数",
        "description": "恒生科技指数，港股科技龙头，包括腾讯、阿里、美团等",
    },
    "恒生指数": {
        "related": ["港股", "香港", "蓝筹", "估值"],
        "parent": "港股指数",
        "description": "香港恒生指数，港股核心基准",
    },

    # 制造业/基建
    "高端装备": {
        "related": ["制造", "制造业", "中证", "指数", "ETF", "基建", "工程机械"],
        "parent": "行业板块",
        "description": "高端装备制造行业，包括航空航天、智能制造、工程机械等",
    },
    "基建": {
        "related": ["基建工程", "中证基建", "工程机械", "固定资产投资", "经济增长"],
        "parent": "行业板块",
        "description": "基础设施建设，包括交通、能源、水利等传统基建和5G、数据中心等新基建",
    },
    "制造": {
        "related": ["高端装备", "制造业", "工业", "工厂", "PMI", "工业增加值"],
        "parent": "宏观经济",
        "description": "制造业是国民经济支柱，PMI和工业增加值是核心观测指标",
    },

    # 环保/碳中和
    "环保": {
        "related": ["碳中和", "绿色", "低碳", "新能源", "ESG"],
        "parent": "行业板块",
        "description": "环保行业，受益于碳中和政策和绿色发展",
    },
    "液冷": {
        "related": ["散热", "数据中心", "AI", "算力", "降温"],
        "parent": "科技细分",
        "description": "液冷散热技术，AI数据中心的关键基础设施",
    },

    # AI/科技细分
    "机器人": {
        "related": ["AI", "人工智能", "自动化", "智能制造", "工业机器人"],
        "parent": "科技细分",
        "description": "机器人行业，包括工业机器人和服务机器人",
    },
    "芯片": {
        "related": ["半导体", "GPU", "算力", "AI", "国产替代"],
        "parent": "科技细分",
        "description": "芯片/半导体产业链，AI算力核心组件",
    },
}


# ══════════════════════════════════════════════════════════════
# 查询扩展
# ══════════════════════════════════════════════════════════════

def expand_query_with_synonyms(query: str) -> str:
    """使用同义词扩展查询（支持中文子串匹配）。"""
    expanded = [query]

    # 按同义词 key 长度降序排列，优先匹配长词（如 "指数基金" 优先于 "基金"）
    sorted_keys = sorted(SYNONYMS.keys(), key=len, reverse=True)

    matched_keys = set()
    for key in sorted_keys:
        # 子串匹配（中文无空格分词）
        if key in query and key not in matched_keys:
            expanded.extend(SYNONYMS[key])
            matched_keys.add(key)
        # 也支持空格分词的英文/拼音
        elif key.lower() in query.lower() and key not in matched_keys:
            expanded.extend(SYNONYMS[key])
            matched_keys.add(key)

    # 去重并保持顺序
    seen = set()
    result = []
    for w in expanded:
        if w not in seen:
            seen.add(w)
            result.append(w)

    return " ".join(result)


def expand_query_with_graph(query: str) -> List[str]:
    """使用知识图谱扩展查询（子串匹配，支持无空格中文）。"""
    expanded = []

    for key in KNOWLEDGE_GRAPH:
        if key in query:
            node = KNOWLEDGE_GRAPH[key]
            expanded.extend(node.get("related", []))

    return list(set(expanded))


def expand_query(query: str, use_synonyms: bool = True, use_graph: bool = True) -> str:
    """综合查询扩展。"""
    expanded = query

    if use_synonyms:
        expanded = expand_query_with_synonyms(expanded)

    if use_graph:
        graph_terms = expand_query_with_graph(query)
        if graph_terms:
            expanded += " " + " ".join(graph_terms)

    return expanded


# ══════════════════════════════════════════════════════════════
# 轻量级重排序
# ══════════════════════════════════════════════════════════════

def tokenize_chinese(text: str) -> List[str]:
    """简单的中文分词（按字和词切分）。"""
    # 简单实现：按空格切分 + 单字切分
    tokens = []
    for word in text.split():
        if len(word) <= 2:
            tokens.append(word)
        else:
            # 长词按 2-gram 切分
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
    return tokens


def calculate_overlap(query_tokens: set, doc_tokens: set) -> float:
    """计算 token 重叠率。"""
    if not query_tokens:
        return 0.0
    intersection = query_tokens & doc_tokens
    return len(intersection) / len(query_tokens)


def calculate_bm25_like(query_tokens: List[str], doc_tokens: List[str],
                         k1: float = 1.5, b: float = 0.75) -> float:
    """简化的 BM25 风格打分。"""
    doc_len = len(doc_tokens)
    avg_doc_len = 100  # 假设平均文档长度

    score = 0.0
    for qt in query_tokens:
        tf = doc_tokens.count(qt)
        if tf > 0:
            idf = 1.0  # 简化：不计算真实 IDF
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
            score += idf * tf_norm

    return score


def lightweight_rerank(query: str, results: List[Dict], top_k: int = 10) -> List[Dict]:
    """轻量级重排序。"""
    if not results:
        return results

    query_tokens = set(tokenize_chinese(query))
    query_tokens_lower = {t.lower() for t in query_tokens}

    for r in results:
        title = r.get("title", "")
        body = r.get("body", "")[:1000]

        title_tokens = set(tokenize_chinese(title))
        body_tokens = set(tokenize_chinese(body))

        # 计算多种相似度
        title_overlap = calculate_overlap(query_tokens, title_tokens)
        body_overlap = calculate_overlap(query_tokens, body_tokens)

        # 标题精确匹配（加分）
        title_exact = 1.0 if query.lower() in title.lower() else 0.0

        # 内容包含查询（加分）
        body_contains = 1.0 if query.lower() in body.lower() else 0.0

        # 综合打分
        r["_rerank_score"] = (
            0.35 * title_overlap +
            0.25 * body_overlap +
            0.25 * title_exact +
            0.15 * body_contains
        )

    # 按重排序分数排序
    results.sort(key=lambda x: x.get("_rerank_score", 0), reverse=True)

    return results[:top_k]


# ══════════════════════════════════════════════════════════════
# 多路召回融合
# ══════════════════════════════════════════════════════════════

def rrf_fusion(routes: List[Tuple[str, List[Dict], float]], k: int = 60) -> List[Dict]:
    """RRF (Reciprocal Rank Fusion) 融合多路召回结果。

    Args:
        routes: [(路由名, 结果列表, 权重), ...]
        k: RRF 参数

    Returns:
        融合后的结果列表
    """
    scores = {}  # key -> (score, result)

    for route_name, results, weight in routes:
        for i, r in enumerate(results):
            key = f"{r.get('content_type', '')}:{r.get('reference_id', '')}"
            rrf_score = weight / (k + i + 1)

            if key in scores:
                scores[key] = (scores[key][0] + rrf_score, scores[key][1])
            else:
                scores[key] = (rrf_score, r)

    # 按分数排序
    sorted_items = sorted(scores.values(), key=lambda x: x[0], reverse=True)

    # 更新分数并返回
    results = []
    for score, r in sorted_items:
        r["_score"] = score
        results.append(r)

    return results


def multi_route_recall(query: str, search_fts_func, search_chroma_func,
                        limit: int = 10) -> List[Dict]:
    """多路召回融合。

    Args:
        query: 用户查询
        search_fts_func: FTS5 搜索函数
        search_chroma_func: ChromaDB 搜索函数
        limit: 返回结果数

    Returns:
        融合后的结果列表
    """
    routes = []

    # 路由 1: 原始查询 FTS5
    fts_results = search_fts_func(query)
    routes.append(("fts_original", fts_results, 1.0))

    # 路由 2: 扩展查询 FTS5
    expanded_query = expand_query(query)
    if expanded_query != query:
        fts_expanded = search_fts_func(expanded_query)
        routes.append(("fts_expanded", fts_expanded, 0.8))

    # 路由 3: 原始查询向量
    chroma_results = search_chroma_func(query)
    routes.append(("chroma_original", chroma_results, 1.0))

    # RRF 融合
    fused = rrf_fusion(routes)

    # 轻量级重排序
    reranked = lightweight_rerank(query, fused, top_k=limit)

    return reranked


# ══════════════════════════════════════════════════════════════
# 查询意图分类
# ══════════════════════════════════════════════════════════════

def classify_query_intent(query: str) -> str:
    """分类查询意图。

    Returns:
        "factual" | "analytical" | "conceptual" | "advisory"
    """
    query_lower = query.lower()

    # 事实查询：问具体数据
    factual_patterns = [
        r"\d+%", r"多少", r"几", r"什么时候", r"最新",
        r"当前", r"现在", r"今天", r"昨天",
    ]
    for pattern in factual_patterns:
        if re.search(pattern, query_lower):
            return "factual"

    # 分析查询：问原因、比较
    analytical_patterns = [
        r"为什么", r"原因", r"区别", r"对比", r"比较",
        r"哪个更", r"怎么样", r"如何",
    ]
    for pattern in analytical_patterns:
        if re.search(pattern, query_lower):
            return "analytical"

    # 建议查询：问该怎么做
    advisory_patterns = [
        r"应该", r"建议", r"推荐", r"怎么买", r"怎么卖",
        r"要不要", r"可以买", r"可以卖",
    ]
    for pattern in advisory_patterns:
        if re.search(pattern, query_lower):
            return "advisory"

    # 概念查询：问定义、解释
    conceptual_patterns = [
        r"是什么", r"什么是", r"解释", r"含义", r"意思",
        r"定义", r"概念",
    ]
    for pattern in conceptual_patterns:
        if re.search(pattern, query_lower):
            return "conceptual"

    return "analytical"  # 默认为分析查询


def get_rrf_params(query: str) -> int:
    """根据查询意图返回 RRF 参数 k。"""
    intent = classify_query_intent(query)

    params = {
        "factual": 30,      # 事实查询：更信任精确匹配
        "analytical": 60,   # 分析查询：平衡
        "conceptual": 100,  # 概念查询：更信任语义
        "advisory": 60,     # 建议查询：平衡
    }

    return params.get(intent, 60)


# ══════════════════════════════════════════════════════════════
# 分层检索（Hierarchical Retrieval）
# ══════════════════════════════════════════════════════════════

def hierarchical_retrieve(query: str, top_k: int = 10,
                           search_fts_func=None, search_chroma_func=None) -> List[Dict]:
    """分层检索：先检索知识分类，再在相关分类中检索具体知识，最后补充案例经验。

    三层检索策略：
    - 第一层：检索知识分类（content_type 分布），确定相关分类
    - 第二层：在相关分类中检索具体知识（FTS + ChromaDB）
    - 第三层：检索相关案例和实战经验（analysis/author_article 类型）

    Args:
        query: 用户查询
        top_k: 最终返回结果数
        search_fts_func: FTS5 搜索函数 (query, content_type, limit) -> (results, dropped)
        search_chroma_func: ChromaDB 搜索函数 (query, content_type, limit) -> (results, dropped)

    Returns:
        融合后的检索结果列表
    """
    if search_fts_func is None or search_chroma_func is None:
        # 默认使用 rag.py 中的函数
        try:
            from services.rag import search_knowledge, search_chroma
            search_fts_func = search_knowledge
            search_chroma_func = search_chroma
        except ImportError:
            logger.warning("hierarchical_retrieve: 无法导入搜索函数")
            return []

    all_results: List[Dict] = []

    # ── 第一层：知识分类检索 ──
    # 用宽松查询获取各 content_type 的分布
    fts_broad, _ = search_fts_func(query, None, limit=20)
    chroma_broad, _ = search_chroma_func(query, None, limit=20)

    # 统计各分类命中数
    type_counts: Dict[str, int] = {}
    for r in fts_broad + chroma_broad:
        ct = r.get("content_type", "")
        type_counts[ct] = type_counts.get(ct, 0) + 1

    # 按命中数排序，取前 3 个最相关分类
    relevant_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    relevant_type_names = [ct for ct, _ in relevant_types if ct]

    logger.info(
        f"hierarchical_retrieve L1: 分类分布={type_counts}, "
        f"相关分类={relevant_type_names}"
    )

    # ── 第二层：在相关分类中检索具体知识 ──
    per_type_limit = max(top_k // max(len(relevant_type_names), 1), 3)
    for ct in relevant_type_names:
        fts_results, _ = search_fts_func(query, ct, per_type_limit)
        chroma_results, _ = search_chroma_func(query, ct, per_type_limit)
        all_results.extend(fts_results)
        all_results.extend(chroma_results)

    # ── 第三层：检索相关案例和实战经验 ──
    # analysis 和 author_article 类型通常包含实战经验
    case_types = [ct for ct in ["analysis", "author_article", "article"] if ct not in relevant_type_names]
    for ct in case_types:
        fts_case, _ = search_fts_func(query, ct, 3)
        if fts_case:
            all_results.extend(fts_case)
            logger.info(f"hierarchical_retrieve L3: {ct} 补充 {len(fts_case)} 条")

    # 去重（按 content_type + reference_id）
    seen = set()
    deduped = []
    for r in all_results:
        key = f"{r.get('content_type', '')}:{r.get('reference_id', '')}"
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # RRF 融合 + 轻量级重排序
    routes = [
        ("hierarchical_fts", [r for r in deduped if r.get("content_type", "") in relevant_type_names], 1.0),
        ("hierarchical_case", [r for r in deduped if r.get("content_type", "") in case_types], 0.7),
    ]
    fused = rrf_fusion(routes, k=60)
    reranked = lightweight_rerank(query, fused, top_k=top_k)

    logger.info(
        f"hierarchical_retrieve 完成: 去重后 {len(deduped)} 条, "
        f"融合重排序后返回 {len(reranked)} 条"
    )
    return reranked


# ══════════════════════════════════════════════════════════════
# 混合召回升级（BM25 + 向量 + 语义重排序）
# ══════════════════════════════════════════════════════════════

# 混合召回权重配置
_HYBRID_WEIGHTS = {
    "bm25": 0.3,       # BM25/FTS5 关键词匹配
    "vector": 0.5,     # 向量语义搜索
    "rerank": 0.2,     # 语义重排序
}


def hybrid_search_enhanced(query: str, top_k: int = 10,
                            search_fts_func=None, search_chroma_func=None,
                            user_id: str = None) -> List[Dict]:
    """增强版混合召回：BM25(0.3) + 向量(0.5) + 语义重排序(0.2)。

    Args:
        query: 用户查询
        top_k: 返回结果数
        search_fts_func: FTS5 搜索函数
        search_chroma_func: ChromaDB 搜索函数
        user_id: 用户 ID（用于个性化加权）

    Returns:
        融合重排序后的结果列表
    """
    if search_fts_func is None or search_chroma_func is None:
        try:
            from services.rag import search_knowledge, search_chroma
            search_fts_func = search_knowledge
            search_chroma_func = search_chroma
        except ImportError:
            logger.warning("hybrid_search_enhanced: 无法导入搜索函数")
            return []

    # 1. BM25/FTS5 检索（权重 0.3）
    fts_results, _ = search_fts_func(query, None, top_k * 2)
    # 2. 向量语义检索（权重 0.5）
    chroma_results, _ = search_chroma_func(query, None, top_k * 2)

    # 3. RRF 融合（加权）
    routes = [
        ("bm25", fts_results, _HYBRID_WEIGHTS["bm25"]),
        ("vector", chroma_results, _HYBRID_WEIGHTS["vector"]),
    ]
    fused = rrf_fusion(routes, k=60)

    # 4. 语义重排序（权重 0.2）
    # 使用轻量级重排序作为语义重排序的代理
    reranked = lightweight_rerank(query, fused, top_k=top_k * 2)

    # 应用 rerank 权重：将 rerank_score 归一化后乘以 0.2，叠加到最终分数
    if reranked:
        max_rerank = max(r.get("_rerank_score", 0) for r in reranked) or 1.0
        for r in reranked:
            normalized_rerank = r.get("_rerank_score", 0) / max_rerank
            r["_final_score"] = r.get("_score", 0) + _HYBRID_WEIGHTS["rerank"] * normalized_rerank
        reranked.sort(key=lambda x: x.get("_final_score", 0), reverse=True)

    # 5. 个性化加权（可选）
    if user_id:
        try:
            from services.rag import _apply_personalization_boost
            _apply_personalization_boost(reranked, user_id)
        except Exception:
            pass

    logger.info(
        f"hybrid_search_enhanced: FTS={len(fts_results)}, Chroma={len(chroma_results)}, "
        f"融合后={len(fused)}, 最终返回={len(reranked[:top_k])}"
    )
    return reranked[:top_k]


def rerank_results(query: str, results: list[dict], top_k: int = 5,
                   use_llm: bool = False) -> list[dict]:
    """对检索结果进行 LLM 轻量重排序。

    使用 LLM 对检索结果打分排序，适用于结果较多时精选最相关的。
    默认不使用 LLM（use_llm=False），使用轻量级规则重排序。

    Args:
        query: 用户查询
        results: 检索结果列表
        top_k: 返回前 k 个
        use_llm: 是否使用 LLM 重排序（增加延迟但更准确）

    Returns:
        重排序后的结果列表
    """
    if not results or len(results) <= 1:
        return results[:top_k]

    if use_llm:
        try:
            from services.llm_service import _call_llm, MODEL

            # 构建重排序 prompt
            doc_summaries = []
            for i, r in enumerate(results[:15]):  # 最多取 15 条
                title = r.get("title", "")[:50]
                body = r.get("body", "")[:200]
                doc_summaries.append(f"[{i}] {title}: {body}")

            prompt = f"""对以下检索结果按与查询的相关性排序，返回最相关的结果编号（从0开始）。

查询：{query}

检索结果：
{chr(10).join(doc_summaries)}

请返回最相关的前 {top_k} 个结果的编号，用逗号分隔，如：0,3,1,5
只输出编号，不要其他文字。"""

            response = _call_llm(
                caller="rag_rerank",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=100,
            )

            raw = (response.choices[0].message.content or "").strip()
            # 解析编号
            indices = []
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit():
                    idx = int(part)
                    if 0 <= idx < len(results):
                        indices.append(idx)

            if indices:
                reranked = [results[i] for i in indices]
                # 补充未选中的结果
                remaining = [r for i, r in enumerate(results) if i not in indices]
                reranked.extend(remaining)
                return reranked[:top_k]

        except Exception as e:
            logger.warning(f"rerank_results LLM 重排序失败，回退到规则重排序: {e}")

    # 默认：使用轻量级规则重排序
    return lightweight_rerank(query, results, top_k=top_k)
