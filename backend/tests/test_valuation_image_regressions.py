"""三类估值图片解析/展示契约回归（无真实模型或网络调用）。"""

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db._conn import _get_conn
from db import valuations
from routers.market import valuation as routes
from services.valuation import valuation_parser as parser


TABLE_DATA = {
    "ok": True, "update_date": "2026-10-16", "market_temperature": 25,
    "count": 1, "data": [{"index_name": "沪深300", "pe": 12.5}],
}


@pytest.fixture
def image_dirs(tmp_path, monkeypatch):
    import config
    from scripts import dd_parse_worker
    for name, directory in [("IMAGES_DIR", "images"), ("DD_IMAGES_DIR", "dd_images"),
                            ("LIUYI_IMAGES_DIR", "liuyi_images"),
                            ("VALUATION_IMAGES_DIR", "valuation_images")]:
        path = tmp_path / "data" / directory
        path.mkdir(parents=True)
        for module in (config, routes, parser, dd_parse_worker):
            monkeypatch.setattr(module, name, path, raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setitem(sys.modules, "services.rag", types.SimpleNamespace(index_valuation=lambda *a: None))
    return tmp_path


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


def test_dd_latest_indexes_endpoint_returns_saved_data(client):
    saved_id = valuations.save_dd_valuation(TABLE_DATA, "data/dd_images/table.jpg")
    response = client.get("/api/valuation/dd/indexes")
    assert response.status_code == 200
    assert response.json()["dd_id"] == saved_id
    assert response.json()["indexes"] == TABLE_DATA["data"]


@pytest.mark.parametrize("kind", ["dd", "liuyi"])
def test_sync_table_parse_accepts_article_image_paths(kind, image_dirs, monkeypatch, client):
    image = image_dirs / "data/images/article/table.jpg"
    image.parent.mkdir()
    image.write_bytes(b"new image")
    cls = routes.DDImageParser if kind == "dd" else routes.LiuyiImageParser
    monkeypatch.setattr(cls, "parse", lambda *a: dict(TABLE_DATA))
    response = client.post(f"/api/valuation/parse-{kind}", json={"path": str(image)})
    assert response.status_code == 200
    record_id = response.json()[f"{kind}_id"]
    record = client.get(f"/api/valuation/{kind}/{record_id}").json()
    assert record["image_path"] == "data/images/article/table.jpg"
    assert record["image_url"] == "/static/images/article/table.jpg"
    assert record["parsed_data"]["data"] == TABLE_DATA["data"]


@pytest.mark.parametrize("kind", ["dd", "liuyi"])
def test_async_table_parse_accepts_database_paths(kind, image_dirs, monkeypatch, client):
    from scripts import dd_parse_worker
    image = image_dirs / f"data/{kind}_images/table.jpg"
    image.write_bytes(b"image")
    cls = dd_parse_worker.DDImageParser if kind == "dd" else dd_parse_worker.LiuyiImageParser
    monkeypatch.setattr(cls, "parse", lambda *a: dict(TABLE_DATA))
    response = client.post(f"/api/valuation/parse-{kind}-async", json={"path": f"data/{kind}_images/table.jpg"})
    assert response.status_code == 200
    # TestClient 的请求事件循环结束前后台任务未必完成；这里仅验证任务使用的真实路径。
    from db.dd_tasks import get_dd_parse_task
    task = get_dd_parse_task(response.json()["task_id"])
    assert task["image_path"] == str(image)


def test_stale_image_parse_task_is_not_returned_as_running(image_dirs):
    """服务重载后遗留的 parsing 任务必须允许下一次点击重新提交。"""
    from db.dd_tasks import create_dd_parse_task, find_running_task, get_dd_parse_task

    image = image_dirs / "data/dd_images/stale.jpg"
    image.write_bytes(b"image")
    task_id = create_dd_parse_task(str(image), parse_type="dd")
    stale_at = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute("UPDATE dd_parse_tasks SET status='parsing', updated_at=? WHERE id=?", (stale_at, task_id))
    conn.commit()
    conn.close()

    assert find_running_task(str(image), parse_type="dd") is None
    assert get_dd_parse_task(task_id)["status"] == "error"


@pytest.mark.parametrize("kind", ["dd", "liuyi"])
def test_table_worker_preserves_article_image_location_without_index_records(kind, image_dirs, monkeypatch):
    from scripts import dd_parse_worker
    from db.dd_tasks import create_dd_parse_task, get_dd_parse_task
    image = image_dirs / "data/images/table.jpg"
    image.write_bytes(b"image")
    cls = dd_parse_worker.DDImageParser if kind == "dd" else dd_parse_worker.LiuyiImageParser
    monkeypatch.setattr(cls, "parse", lambda *a: dict(TABLE_DATA))
    task_id = create_dd_parse_task(str(image), parse_type=kind)
    asyncio.run(dd_parse_worker.run_dd_parse(task_id, str(image), kind))
    task = get_dd_parse_task(task_id)
    assert task["status"] == "done"
    record = (valuations.get_dd_valuation if kind == "dd" else valuations.get_liuyi_valuation)(task["dd_id"])
    assert record["image_url"] == "/static/images/table.jpg"
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM analysis_records").fetchone()[0] == 0
    conn.close()


def test_empty_single_image_result_is_failure_without_database_writes(image_dirs, monkeypatch):
    image = image_dirs / "data/images/new.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(parser.ImageParser, "parse", lambda *a: {"index_name": None, "current_value": None})
    result = parser.parse_single_valuation(str(image))
    assert result["ok"] is False
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM index_valuations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM analysis_records").fetchone()[0] == 0
    conn.close()


def test_single_parse_name_collision_keeps_new_image_and_reuses_record(image_dirs, monkeypatch):
    image = image_dirs / "data/images/new.jpg"
    image.write_bytes(b"new image")
    old_image = image.parent / "沪深300_市盈率.jpg"
    old_image.write_bytes(b"old image")
    conn = _get_conn()
    conn.execute("INSERT INTO analysis_records (image_path, image_url, status) VALUES (?, ?, 'pending')",
                 ("data/images/new.jpg", "/static/images/new.jpg"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(parser.ImageParser, "parse", lambda *a: {
        "index_code": "000300.CSI", "index_name": "沪深300", "metric_type": "市盈率", "current_value": 12.5,
    })
    result = parser.parse_single_valuation(str(image), snapshot_date="2026-10-16")
    assert result["ok"] is True
    record = valuations.get_latest_valuation("000300")
    from pathlib import Path
    assert Path(record["source_image"]).read_bytes() == b"new image"
    assert old_image.read_bytes() == b"old image"
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM analysis_records").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"


def test_explicit_sub_one_percent_survives_save_and_read():
    valuations.save_valuation({"index_code": "000300", "index_name": "沪深300", "percentile": "0.5%"})
    assert valuations.get_latest_valuation("000300")["percentile"] == 0.5
    assert valuations.list_valuation_indexes()[0]["percentile"] == 0.5
    assert valuations.get_valuation_history("000300")[0]["percentile"] == 0.5
    assert valuations.get_best_valuation("000300", enable_online=False)["percentile"] == 0.5


def test_upsert_returns_matching_metric_id():
    pe_id = valuations.save_valuation({"index_code": "000300", "metric_type": "市盈率"})
    pb_id = valuations.save_valuation({"index_code": "000300", "metric_type": "市净率"})
    updated_id = valuations.save_valuation({"index_code": "000300", "metric_type": "市净率", "current_value": 1.5})
    assert pe_id != pb_id
    assert updated_id == pb_id
    assert valuations.save_valuation({"index_code": "000300", "metric_type": "市盈率", "current_value": 12.5}) == pe_id


def test_history_matches_normalized_and_legacy_suffix_codes():
    valuations.save_valuation({"index_code": "000300.CSI", "index_name": "沪深300", "current_value": 12.5},
                             snapshot_date="2026-10-16")
    conn = _get_conn()
    conn.execute("INSERT INTO index_valuations (index_code,index_name,snapshot_date,metric_type,current_value) VALUES (?, ?, ?, ?, ?)",
                 ("000300.CSI", "沪深300", "2026-10-15", "市盈率", 12.4))
    conn.commit()
    conn.close()
    assert len(valuations.get_valuation_history("000300.CSI")) == 2
    assert len(valuations.get_valuation_history("000300", metric_type="市盈率")) == 2


def test_different_table_types_do_not_deduplicate_each_other(image_dirs, monkeypatch, client):
    from db.dd_tasks import create_dd_parse_task, get_dd_parse_task
    from scripts import dd_parse_worker
    image = image_dirs / "data/images/table.jpg"
    image.write_bytes(b"image")
    dd_id = create_dd_parse_task(str(image), parse_type="dd")
    monkeypatch.setattr(dd_parse_worker.LiuyiImageParser, "parse", lambda *a: dict(TABLE_DATA))
    response = client.post("/api/valuation/parse-liuyi-async", json={"path": str(image)})
    assert response.status_code == 200
    assert response.json()["task_id"] != dd_id
    assert get_dd_parse_task(response.json()["task_id"])["parse_type"] == "liuyi"


def test_generic_dd_parse_does_not_create_single_index_record(image_dirs, monkeypatch):
    image = image_dirs / "data/dd_images/table.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(parser.DDImageParser, "parse", lambda *a: dict(TABLE_DATA))
    assert parser.parse_single_valuation("data/dd_images/table.jpg")["ok"] is True
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM dd_valuations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM analysis_records").fetchone()[0] == 0
    conn.close()


def test_single_dividend_yield_accepts_explicit_percentage_value(image_dirs, monkeypatch):
    image = image_dirs / "data/images/dividend.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(parser.ImageParser, "parse", lambda *a: {
        "index_code": "000300", "index_name": "沪深300", "metric_type": "股息率", "current_value": "4.5%",
    })
    result = parser.parse_single_valuation(str(image))
    assert result["ok"] is True
    assert valuations.get_latest_valuation("000300")["current_value"] == 4.5


@pytest.mark.parametrize("extra", [{"percentile": "25%"}, {"background_color": "绿色"}])
def test_simplified_single_index_card_does_not_require_current_value(extra, image_dirs, monkeypatch):
    image = image_dirs / "data/images/card.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(parser.ImageParser, "parse", lambda *a: {
        "index_code": "000300", "index_name": "沪深300", "metric_type": "市盈率", **extra,
    })
    assert parser.parse_single_valuation(str(image))["ok"] is True
    record = valuations.get_latest_valuation("000300")
    assert record["current_value"] is None
    for key in extra:
        assert record[key] == (25.0 if key == "percentile" else extra[key])


@pytest.mark.parametrize("result", [None, [], {}, {"index_code": " ", "index_name": "空白"},
                                   {"index_code": "UNKNOWN", "index_name": "未知指数"}])
def test_invalid_single_index_identity_is_rejected(result, image_dirs, monkeypatch):
    image = image_dirs / "data/images/card.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(parser.ImageParser, "parse", lambda *a: result)
    assert parser.parse_single_valuation(str(image))["ok"] is False
    assert valuations.list_valuation_indexes() == []


@pytest.mark.parametrize("kind", ["dd", "liuyi"])
def test_chinese_table_payload_is_normalized_before_storage(kind, image_dirs, monkeypatch, client):
    from services.content import image_parser
    from PIL import Image
    image = image_dirs / f"data/{kind}_images/table.jpg"
    Image.new('RGB', (32, 32), 'white').save(image)
    monkeypatch.setattr(image_parser, "_call_vision", lambda *a, **k: json.dumps({
        "更新日期": "2026年10月16日", "市场温度": "25度", "数据": [{
            "指数名称": "沪深300", "指数代码": "000300", "PE": "12.5", "PB": 1.5,
            "PE百分位": "0.5%", "PB百分位": "25%", "股息率": "3.5%", "背景颜色": "绿色",
        }],
    }))
    response = client.post(f"/api/valuation/parse-{kind}", json={"path": f"data/{kind}_images/table.jpg"})
    assert response.status_code == 200
    record = client.get(f"/api/valuation/{kind}/{response.json()[f'{kind}_id']}").json()
    assert record["update_date"] == "2026-10-16"
    assert record["market_temperature"] == 25
    assert record["parsed_data"]["data"][0]["pe_percentile"] == 0.5
    assert record["parsed_data"]["data"][0]["index_name"] == "沪深300"


@pytest.mark.parametrize("kind", ["dd", "liuyi"])
def test_empty_chinese_table_payload_is_not_persisted(kind, image_dirs, monkeypatch, client):
    from services.content import image_parser
    from PIL import Image
    image = image_dirs / f"data/{kind}_images/table.jpg"
    Image.new('RGB', (32, 32), 'white').save(image)
    monkeypatch.setattr(image_parser, "_call_vision", lambda *a, **k: '{"数据": []}')
    response = client.post(f"/api/valuation/parse-{kind}", json={"path": f"data/{kind}_images/table.jpg"})
    assert response.json()["ok"] is False
    assert (valuations.list_dd_valuations if kind == "dd" else valuations.list_liuyi_valuations)() == []
