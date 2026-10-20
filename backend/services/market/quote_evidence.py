"""把东财命名列转为带标的和时间的点位证据，保留完整原始响应。"""
import math
import re


def extract_market_observations(data):
    results = []
    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            table, names = value.get("table"), value.get("nameMap")
            if isinstance(table, dict) and isinstance(names, dict):
                entity = value.get("entityName") or ""
                code = value.get("code")
                stamps = table.get("headName") or []
                if isinstance(stamps, list) and entity and code:
                    for key, label in names.items():
                        if label not in {"最新价", "最新点位", "指数点位"}:
                            continue
                        values = table.get(key)
                        if not isinstance(values, list):
                            continue
                        for raw, stamp in zip(values, stamps):
                            try:
                                point = float(str(raw).replace(",", ""))
                                if not math.isfinite(point) or point <= 0 or not re.match(r"\d{4}-\d{2}-\d{2}", str(stamp)):
                                    continue
                            except (ValueError, TypeError):
                                continue
                            results.append({"index_code": code, "index_name": entity.split("(", 1)[0],
                                            "current_point": point, "as_of": stamp,
                                            "source": "eastmoney", "source_field": key})
                            if len(results) >= 40:
                                return
            if len(results) < 40:
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        visit(child)
    visit(data)
    return results[:40]
