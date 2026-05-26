"""估值趋势图 — 生成 PE/PB 分位点走势图"""

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无头模式
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# 将 scripts 目录加入 path 以便导入 query_valuation
sys.path.insert(0, str(Path(__file__).parent))
from query_valuation import get_history, find_db


def _get_field(v: dict, new_field: str, old_fields: list[str]):
    """兼容新旧 schema 取值。"""
    if new_field in v and v[new_field] is not None:
        return v[new_field]
    for f in old_fields:
        if f in v and v[f] is not None:
            return v[f]
    return None


def plot_pe_trend(index_code: str, days: int = 90, output: str = None,
                  metric_type: str = None) -> str:
    """
    绘制估值趋势图，返回图片保存路径。
    图中包含：当前值折线、分位点、危险值/中位数/机会值参考线。
    """
    if not metric_type:
        # 自动检测：先看新 schema 有哪些类型，fallback 旧 schema
        history = get_history(index_code, min(days, 1))
        if history and history[0].get("metric_type"):
            metric_type = history[0]["metric_type"]
        else:
            metric_type = "市盈率"

    history = get_history(index_code, days, metric_type=metric_type)
    # 如果没数据且没指定 metric_type，尝试不加筛选
    if not history and metric_type:
        history = get_history(index_code, days)

    if not history:
        # 尝试旧 schema（无 metric_type 列）
        history = get_history(index_code, days)
        if not history:
            raise ValueError(f"未找到 {index_code} 的估值数据")

    index_name = history[0].get("index_name", index_code)
    # 判断是否有 metric_type（新 schema）
    has_metric = history[0].get("metric_type") is not None
    current_type = history[0].get("metric_type", "市盈率")

    dates = []
    current_values = []
    percentile_values = []

    for v in history:
        try:
            d = datetime.strptime(v["snapshot_date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        dates.append(d)
        if has_metric:
            current_values.append(v.get("current_value"))
            percentile_values.append(v.get("percentile"))
        else:
            current_values.append(_get_field(v, None, ["pe_ttm", "pb"]))
            percentile_values.append(_get_field(v, None, ["pe_percentile", "pb_percentile"]))

    if not dates:
        raise ValueError(f"{index_code} 无有效日期数据")

    # 参考线值
    latest = history[-1]
    if has_metric:
        danger = latest.get("danger_value")
        median = latest.get("median")
        opportunity = latest.get("opportunity_value")
    else:
        danger = _get_field(latest, None, ["pe_danger", "pb_danger"])
        median = _get_field(latest, None, ["pe_median", "pb_median"])
        opportunity = _get_field(latest, None, ["pe_opportunity", "pb_opportunity"])

    type_label = {"市盈率": "PE", "市净率": "PB"}.get(current_type, current_type)

    # 绘图
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                     gridspec_kw={"height_ratios": [2, 1]})
    fig.suptitle(f"{index_name} ({index_code}) {current_type}趋势", fontsize=14, fontweight="bold")

    # 上图：当前值
    ax1.plot(dates, current_values, color="#1f77b4", linewidth=2, label=f"{type_label}-TTM")
    if danger:
        ax1.axhline(y=danger, color="#d62728", linestyle="--", alpha=0.7, label=f"危险值 ({danger})")
    if median:
        ax1.axhline(y=median, color="#ff7f0e", linestyle="--", alpha=0.7, label=f"中位数 ({median})")
    if opportunity:
        ax1.axhline(y=opportunity, color="#2ca02c", linestyle="--", alpha=0.7, label=f"机会值 ({opportunity})")

    ax1.set_ylabel(f"{type_label} 当前值")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.3)

    if current_values and current_values[-1] is not None:
        ax1.annotate(f"{current_values[-1]:.2f}",
                     xy=(dates[-1], current_values[-1]),
                     xytext=(10, 10), textcoords="offset points",
                     fontsize=10, fontweight="bold", color="#1f77b4",
                     arrowprops=dict(arrowstyle="->", color="#1f77b4"))

    # 下图：分位点
    ax2.fill_between(dates, percentile_values, alpha=0.3, color="#17becf")
    ax2.plot(dates, percentile_values, color="#17becf", linewidth=1.5, label=f"{type_label} 分位点 (%)")
    ax2.axhline(y=20, color="#2ca02c", linestyle=":", alpha=0.5, label="低估区 (<20%)")
    ax2.axhline(y=80, color="#d62728", linestyle=":", alpha=0.5, label="高估区 (>80%)")
    ax2.set_ylabel("分位点 (%)")
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, alpha=0.3)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45)

    plt.tight_layout()

    if not output:
        output = f"{index_code}_trend.png"
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output


def plot_multi_index(index_codes: list[str], days: int = 90, output: str = None,
                     metric_type: str = None) -> str:
    """绘制多个指数分位点对比图。"""
    fig, ax = plt.subplots(figsize=(12, 6))

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
              "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

    for i, code in enumerate(index_codes):
        history = get_history(code, days, metric_type=metric_type)
        if not history:
            history = get_history(code, days)
        if not history:
            print(f"跳过 {code}: 无数据")
            continue

        has_metric = history[0].get("metric_type") is not None
        dates = []
        values = []
        for v in history:
            try:
                d = datetime.strptime(v["snapshot_date"], "%Y-%m-%d")
                dates.append(d)
                if has_metric:
                    values.append(v.get("percentile"))
                else:
                    values.append(_get_field(v, None, ["pe_percentile", "pb_percentile"]))
            except (ValueError, TypeError):
                continue

        name = history[0].get("index_name", code)
        color = colors[i % len(colors)]
        ax.plot(dates, values, color=color, linewidth=1.5, label=f"{name} ({code})")

    ax.axhline(y=20, color="#2ca02c", linestyle=":", alpha=0.5)
    ax.axhline(y=80, color="#d62728", linestyle=":", alpha=0.5)
    ax.set_ylabel("分位点 (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.xticks(rotation=45)

    plt.title("指数分位点对比", fontsize=14, fontweight="bold")
    plt.tight_layout()

    if not output:
        output = "comparison_trend.png"
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python plot_trends.py <指数代码> [天数] [输出文件]")
        print("  python plot_trends.py --compare <代码1> <代码2> ... [天数]")
        print("示例:")
        print("  python plot_trends.py 931468.CSI")
        print("  python plot_trends.py --compare 931468.CSI 000905.SH 60")
        sys.exit(1)

    if sys.argv[1] == "--compare":
        codes = []
        days = 90
        for arg in sys.argv[2:]:
            if arg.isdigit():
                days = int(arg)
            else:
                codes.append(arg)
        if not codes:
            print("请指定至少一个指数代码")
            sys.exit(1)
        out = plot_multi_index(codes, days)
        print(f"对比图已保存: {out}")
    else:
        code = sys.argv[1]
        days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 90
        output = sys.argv[3] if len(sys.argv) > 3 else None
        out = plot_pe_trend(code, days, output)
        print(f"趋势图已保存: {out}")
