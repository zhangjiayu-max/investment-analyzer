"""文章图片后台任务回归：临时数据库，无模型及网络调用。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from db import (
    create_article, create_analysis_record, get_article, get_analysis_record,
    get_analysis_records, update_article, update_analysis_record,
)
from routers.knowledge import articles as routes


VALID_RESULT = {"index_code": "000300", "index_name": "沪深300", "metric_type": "市盈率", "current_value": 12.5}


@pytest.fixture(autouse=True)
def isolated_background(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "ROOT", tmp_path)
    monkeypatch.setattr(routes, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(routes, "_analyze_progress", {})
    monkeypatch.setattr(routes, "_analyze_cancel", set())
    monkeypatch.setattr(routes, "_analyze_tasks", {})
    monkeypatch.setattr(routes, "_reanalyze_tasks", {})
    monkeypatch.setattr(routes, "_vision_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(routes, "save_valuation", Mock(return_value=1))


def make_article(tmp_path, statuses=("pending",)):
    article_id = create_article("https://example.com/article", "测试文章", "2026-10-16")
    update_article(article_id, status="analyzing")
    record_ids = []
    for i, status in enumerate(statuses):
        image = tmp_path / f"{i}.jpg"
        image.write_bytes(b"image")
        rid = create_analysis_record(article_id, i, str(image), f"https://example.com/{i}.jpg")
        update_analysis_record(rid, status=status)
        record_ids.append(rid)
    routes._analyze_progress[article_id] = {
        "total": len(statuses), "done": 0, "success": 0, "failed": 0, "current_record_id": None,
    }
    return article_id, record_ids


def test_add_article_all_vision_failures_set_article_error(tmp_path, monkeypatch):
    article_id = create_article("https://example.com/article")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")

    async def fetch(url):
        return {"title": "测试文章", "publish_time": "2026-10-16", "images": ["https://example.com/image.jpg"]}

    async def download(images, directory):
        return [image]

    def parse(*args):
        raise RuntimeError("视觉服务鉴权失败（401）")

    monkeypatch.setattr(routes, "fetch_article", fetch)
    monkeypatch.setattr(routes, "download_images", download)
    monkeypatch.setattr(routes.ImageParser, "parse", parse)
    asyncio.run(routes._background_add_article(article_id, "https://example.com/article"))
    assert get_article(article_id)["status"] == "error"
    assert "401" in get_analysis_records(article_id)[0]["error_msg"]
    assert article_id not in routes._analyze_progress
    routes.save_valuation.assert_not_called()


@pytest.mark.parametrize("result", [None, [], {"index_code": ""}, {"index_code": "UNKNOWN"}, {"index_code": "   "}])
def test_invalid_parser_result_is_reported_without_valuation_write(tmp_path, monkeypatch, result):
    article_id, (rid,) = make_article(tmp_path)
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: result)
    asyncio.run(routes._background_analyze(article_id))
    record = get_analysis_record(rid)
    assert record["status"] == "error"
    assert record["error_msg"] == "AI 未能提取到数据"
    assert get_article(article_id)["status"] == "error"
    routes.save_valuation.assert_not_called()


def test_cancel_preserves_error_even_when_earlier_images_succeeded(tmp_path, monkeypatch):
    article_id, record_ids = make_article(tmp_path, ("success", "pending", "timeout"))
    routes._analyze_cancel.add(article_id)
    asyncio.run(routes._background_analyze(article_id))
    article = get_article(article_id)
    assert article["status"] == "error"
    assert article["error_msg"] == "用户取消分析"
    assert [get_analysis_record(rid)["status"] for rid in record_ids] == ["success", "cancelled", "cancelled"]
    assert article_id not in routes._analyze_cancel


def test_cancel_during_parse_does_not_get_overwritten(tmp_path, monkeypatch):
    article_id, record_ids = make_article(tmp_path, ("success", "pending", "timeout"))

    async def scenario():
        started = asyncio.Event()

        async def fake_to_thread(*args):
            started.set()
            await asyncio.Future()

        monkeypatch.setattr(routes.asyncio, "to_thread", fake_to_thread)
        task = asyncio.create_task(routes._background_analyze(article_id))
        await started.wait()
        await routes.cancel_analyze(article_id)
        await task

    asyncio.run(scenario())
    assert get_article(article_id)["error_msg"] == "用户取消分析"
    assert get_article(article_id)["status"] == "error"
    assert [get_analysis_record(rid)["status"] for rid in record_ids] == ["success", "cancelled", "cancelled"]
    assert not routes._analyze_progress
    assert not routes._analyze_tasks
    routes.save_valuation.assert_not_called()


def test_cancel_after_last_image_preserves_cancelled_article(tmp_path, monkeypatch):
    article_id, _ = make_article(tmp_path)
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: dict(VALID_RESULT))

    async def cancel_during_pause(delay):
        await routes.cancel_analyze(article_id)

    monkeypatch.setattr(routes.asyncio, "sleep", cancel_during_pause)
    asyncio.run(routes._background_analyze(article_id))
    assert get_article(article_id)["status"] == "error"
    assert get_article(article_id)["error_msg"] == "用户取消分析"


@pytest.mark.parametrize("result, expected", [(VALID_RESULT, "analyzed"), (None, "error")])
def test_reanalyze_refreshes_parent_status(tmp_path, monkeypatch, result, expected):
    article_id, (rid,) = make_article(tmp_path)
    routes._analyze_progress.pop(article_id)
    update_article(article_id, status="error", error_msg="旧错误")
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: result)
    asyncio.run(routes._background_reanalyze(rid))
    assert get_article(article_id)["status"] == expected
    assert get_article(article_id)["error_msg"] != "旧错误"


@pytest.mark.parametrize("result", [None, [], {"index_code": "UNKNOWN"}, {"index_code": "   "}])
def test_reanalyze_invalid_results_do_not_write_valuations(tmp_path, monkeypatch, result):
    article_id, (rid,) = make_article(tmp_path)
    routes._analyze_progress.pop(article_id)
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: result)
    asyncio.run(routes._background_reanalyze(rid))
    assert get_analysis_record(rid)["status"] == "error"
    assert get_article(article_id)["status"] == "error"
    routes.save_valuation.assert_not_called()


def test_valid_index_without_current_value_remains_supported(tmp_path, monkeypatch):
    article_id, (rid,) = make_article(tmp_path)
    result = {**VALID_RESULT, "current_value": None}
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: result)
    asyncio.run(routes._background_analyze(article_id))
    assert get_analysis_record(rid)["status"] == "success"
    assert get_analysis_record(rid)["error_msg"] == "AI 返回空值（无当前值）"
    routes.save_valuation.assert_called_once()


def test_partial_success_retains_failed_image_summary(tmp_path, monkeypatch):
    article_id, _ = make_article(tmp_path, ("success", "pending"))
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: None)
    asyncio.run(routes._background_analyze(article_id))
    assert get_article(article_id)["status"] == "analyzed"
    assert get_article(article_id)["error_msg"] == "1 张图片分析失败"


@pytest.mark.parametrize("automatic", [False, True])
def test_partial_download_keeps_original_image_urls_and_indexes(tmp_path, monkeypatch, automatic):
    urls = [f"https://example.com/{i}.jpg" for i in range(3)]
    article_id = create_article("https://example.com/article", "测试文章", "2026-10-16")

    async def fetch(url):
        return {"title": "测试文章", "publish_time": "2026-10-16", "images": urls}

    async def download(images, directory):
        paths = [Path(directory) / f"img_{i:03d}.jpg" for i in (0, 2)]
        for path in paths:
            path.write_bytes(b"image")
        return [str(path) for path in paths]

    monkeypatch.setattr(routes, "fetch_article", fetch)
    monkeypatch.setattr(routes, "download_images", download)
    monkeypatch.setattr(routes.ImageParser, "parse", lambda *args: dict(VALID_RESULT))
    if automatic:
        asyncio.run(routes._background_add_article(article_id, "https://example.com/article"))
    else:
        asyncio.run(routes._background_download(article_id, get_article(article_id)))

    records = get_analysis_records(article_id)
    assert [(r["image_index"], r["image_url"]) for r in records] == [(0, urls[0]), (2, urls[2])]
    article = get_article(article_id)
    manifest = json.loads(Path(article["manifest_path"]).read_text())
    assert [(r["index"], r["url"]) for r in manifest["images"]] == [(0, urls[0]), (2, urls[2])]
    assert article["image_count"] == 2


@pytest.mark.parametrize("automatic", [False, True])
def test_all_downloads_failed_sets_error_without_starting_analysis(tmp_path, monkeypatch, automatic):
    article_id = create_article("https://example.com/article", "测试文章", "2026-10-16")

    async def fetch(url):
        return {"title": "测试文章", "publish_time": "2026-10-16", "images": ["https://example.com/0.jpg"]}

    async def download(images, directory):
        return []

    parse = Mock(return_value=dict(VALID_RESULT))
    monkeypatch.setattr(routes, "fetch_article", fetch)
    monkeypatch.setattr(routes, "download_images", download)
    monkeypatch.setattr(routes.ImageParser, "parse", parse)
    if automatic:
        asyncio.run(routes._background_add_article(article_id, "https://example.com/article"))
    else:
        asyncio.run(routes._background_download(article_id, get_article(article_id)))

    article = get_article(article_id)
    assert article["status"] == "error"
    assert article["error_msg"] == "图片全部下载失败（1 张）"
    assert article["image_count"] == 0
    assert get_analysis_records(article_id) == []
    parse.assert_not_called()
    assert not routes._analyze_progress
