"""领先指标数据源统一抽象层。

设计目标：将政策草案/资本开支/产业资本等领先指标作为第一类公民接入系统，
解决"只看新闻无法预估未来"的问题。

核心概念：
- LeadingSignal: 统一信号数据结构
- LeadingIndicatorProvider: 数据源抽象接口，所有 Provider 实现此接口
- leading_level: 领先级别 strong(6-24月) / medium(1-3月) / weak(1-3年)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LeadingSignal:
    """领先指标信号统一数据结构。"""
    signal_type: str          # policy_draft / capex_announcement / insider_trading / customs_data / pmi_subitem
    leading_level: str        # strong (6-24月) / medium (1-3月) / weak (1-3年)
    title: str
    summary: str
    source_url: str
    publish_date: str         # YYYY-MM-DD
    affected_sectors: list = field(default_factory=list)   # 对齐 event_radar.SECTOR_TO_INDEX 的标准板块名
    affected_themes: list = field(default_factory=list)     # 主题关键词
    direction: str = "neutral"  # positive / negative / neutral
    confidence: float = 0.5     # 0-1
    raw_data: dict = field(default_factory=dict)   # 原始数据，供 LLM 二次分析
    # 量化字段（可选，L2 指标填充）
    metric_value: Optional[float] = None    # 如 PMI=56.3、出口同比+12.5%
    metric_unit: str = ""                   # % / 亿元 / 万辆
    metric_yoy: Optional[float] = None      # 同比
    metric_mom: Optional[float] = None      # 环比


class LeadingIndicatorProvider(ABC):
    """领先指标数据源统一接口。所有数据源实现此接口。"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """数据源标识（如 policy_draft / capex / insider_trading）。"""

    @property
    @abstractmethod
    def leading_level(self) -> str:
        """领先级别：strong / medium / weak。"""

    @abstractmethod
    def fetch(self, lookback_days: int = 7) -> list:
        """拉取近 N 天的领先指标信号。子类实现具体抓取逻辑。"""

    def map_to_sectors(self, text: str) -> list:
        """文本 → 板块映射。复用 event_radar 板块归一化逻辑。

        通过关键词匹配 SECTOR_TO_INDEX 中的板块名，
        并用 _SECTOR_TO_NAME_KEYWORDS 做同义词扩展。
        """
        if not text:
            return []
        from services.market.event_radar import (
            SECTOR_TO_INDEX,
            _SECTOR_ALIASES,
            _SECTOR_TO_NAME_KEYWORDS,
            _normalize_sector,
        )
        matched = set()
        text_lower = text.lower()
        # 遍历板块关键词表
        for sector, keywords in _SECTOR_TO_NAME_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    # 归一化（处理别名）
                    normalized = _normalize_sector(sector)
                    if normalized in SECTOR_TO_INDEX:
                        matched.add(normalized)
                    break
        # 同时检查文本是否直接包含标准板块名或别名
        for alias, target in _SECTOR_ALIASES.items():
            if alias in text:
                matched.add(target)
        for sector in SECTOR_TO_INDEX:
            if sector in text:
                matched.add(sector)
        return sorted(matched)
