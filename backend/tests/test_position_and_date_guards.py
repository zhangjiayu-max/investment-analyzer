"""持仓约束冲突守卫 / 行情数据时效守卫的契约测试。

对应 2026-10-21 分析记录人工复核发现的两类硬伤：
- log#139：一边认定权益超 KYC 目标 5pct，一边建议"加仓到 30%"
- log#139/140：同一天两份报告都写"今日"，行情数字却相反（库内只有前一日收盘）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_config(monkeypatch, module, value=True):
    import db.config as cfg
    monkeypatch.setattr(cfg, 'get_config_bool', lambda key, default=None: value, raising=False)
    monkeypatch.setattr(module, 'get_config_bool', lambda key, default=None: value, raising=False)


# ── 持仓约束冲突守卫 ──────────────────────────────────────────────

CONFLICT_ANSWER = """# 每日市场简报

### 6. 今日行动建议

当前权益仓位20.2%已超15%目标，建议每次买入不超过5万元，买入后权益占比维持在30%以内。
"""


def test_position_guard_flags_conflict(monkeypatch):
    from agent.safety import position_constraint_guard as mod
    _stub_config(monkeypatch, mod)
    out, warnings = mod.enforce_position_constraint_guard(CONFLICT_ANSWER)
    assert 'position_conflict' in warnings
    assert out != CONFLICT_ANSWER
    assert '持仓约束冲突提示' in out


def test_position_guard_allows_conditional_add(monkeypatch):
    from agent.safety import position_constraint_guard as mod
    _stub_config(monkeypatch, mod)
    answer = """# 报告

### 操作建议

权益20.2%已超15%目标。若坚持补仓，只在用户明确同意上调权益目标后，从条件清单第一行开始，
单笔不超过1%总资产。先解决单只超限，再谈加仓。
"""
    out, warnings = mod.enforce_position_constraint_guard(answer)
    assert warnings == []
    assert out == answer


def test_position_guard_ignores_no_overweight(monkeypatch):
    from agent.safety import position_constraint_guard as mod
    _stub_config(monkeypatch, mod)
    answer = """# 报告

### 操作建议

建议买入红利质量 10000 元，分批建仓。
"""
    out, warnings = mod.enforce_position_constraint_guard(answer)
    assert warnings == []
    assert out == answer


def test_position_guard_respects_switch(monkeypatch):
    from agent.safety import position_constraint_guard as mod
    _stub_config(monkeypatch, mod, value=False)
    out, warnings = mod.enforce_position_constraint_guard(CONFLICT_ANSWER)
    assert warnings == []
    assert out == CONFLICT_ANSWER


# ── 行情数据时效守卫 ──────────────────────────────────────────────

TODAY_ANSWER = """今日上证指数收涨 +0.12%，创业板指 -0.26%，成交 14715 亿元。
"""


def test_market_date_guard_flags_stale(monkeypatch):
    from agent.safety import market_date_guard as mod
    _stub_config(monkeypatch, mod)
    monkeypatch.setattr(mod, '_latest_trade_date', lambda: '2026-10-20')
    monkeypatch.setattr(mod.datetime, 'date', type(
        'D', (), {'today': staticmethod(lambda: type('X', (), {'isoformat': lambda self: '2026-10-21'})())}
    ))
    out, warnings = mod.enforce_market_date_guard(TODAY_ANSWER)
    assert warnings and warnings[0].startswith('stale_market_date')
    assert '数据时效提示' in out
    assert '2026-10-20' in out


def test_market_date_guard_passes_when_fresh(monkeypatch):
    from agent.safety import market_date_guard as mod
    _stub_config(monkeypatch, mod)
    monkeypatch.setattr(mod, '_latest_trade_date', lambda: '2026-10-21')
    # 必须同时锚定"今天"：该用例若依赖真实日期，跨午夜后 today=次日，
    # latest(10-21) < today(10-22) 会误报 stale（曾于 10-22 凌晨稳定失败）。
    monkeypatch.setattr(mod.datetime, 'date', type(
        'D', (), {'today': staticmethod(lambda: type('X', (), {'isoformat': lambda self: '2026-10-21'})())}
    ))
    out, warnings = mod.enforce_market_date_guard(TODAY_ANSWER)
    assert warnings == []
    assert out == TODAY_ANSWER


def test_market_date_guard_ignores_non_market_today(monkeypatch):
    from agent.safety import market_date_guard as mod
    _stub_config(monkeypatch, mod)
    monkeypatch.setattr(mod, '_latest_trade_date', lambda: '2026-10-20')
    answer = '历史新闻不作为今日事件，仅作背景参考。'
    out, warnings = mod.enforce_market_date_guard(answer)
    assert warnings == []
    assert out == answer
