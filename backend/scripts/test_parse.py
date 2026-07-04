#!/usr/bin/env python3
"""测试图片解析功能，查看解析结果数据结构。"""

import json
import sys
from pathlib import Path

# 添加 backend 到 path
sys.path.insert(0, str(Path(__file__).parent))

from services.image_parser import ImageParser, DDImageParser


def test_single_parse(image_path: str):
    """测试单指数估值图解析。"""
    print(f"\n{'='*60}")
    print(f"测试单指数解析: {image_path}")
    print('='*60)

    parser = ImageParser()
    result = parser.parse(image_path)

    print("\n解析结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n字段说明:")
    fields = {
        "index_code": "指数代码",
        "index_name": "指数名称",
        "current_point": "当前点位",
        "change_pct": "涨跌幅",
        "metric_type": "指标类型 (市盈率/市净率/...)",
        "current_value": "当前值",
        "percentile": "百分位 (%)",
        "danger_value": "危险值",
        "median": "中位数",
        "opportunity_value": "机会值",
        "max_value": "最大值",
        "min_value": "最小值",
        "avg_value": "平均值",
        "zscore": "Z分数",
    }
    for k, v in fields.items():
        val = result.get(k)
        print(f"  {k}: {val}  ({v})")


def test_dd_parse(image_path: str):
    """测试螺丝钉估值表解析（多指数）。"""
    print(f"\n{'='*60}")
    print(f"测试螺丝钉估值表解析: {image_path}")
    print('='*60)

    parser = DDImageParser()
    result = parser.parse(image_path)

    print("\n解析结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("ok"):
        print(f"\n基本信息:")
        print(f"  更新日期: {result.get('update_date')}")
        print(f"  市场温度: {result.get('market_temperature')}")
        print(f"  指数数量: {result.get('count')}")

        if result.get("data"):
            print(f"\n第一个指数数据示例:")
            print(json.dumps(result["data"][0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_parse.py <图片路径> [dd]")
        print("  dd: 使用螺丝钉估值表解析模式")
        sys.exit(1)

    image_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "single"

    if mode == "dd":
        test_dd_parse(image_path)
    else:
        test_single_parse(image_path)
