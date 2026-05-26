---
name: download-wechat-images
description: 下载微信公众号文章中的所有图片到本地，支持 manifest 去重。当用户发送包含 mp.weixin.qq.com 的链接时触发。
---

# 公众号图片下载

将微信公众号文章中的图片下载到本地目录，支持去重。

## 触发条件

用户消息中包含微信公众号链接（`mp.weixin.qq.com`）时自动触发。

## 功能

1. **下载图片** — 抓取文章，下载所有正文图片到本地
2. **manifest 去重** — 记录 URL→本地路径映射，重复运行自动跳过已下载图片
3. **自动提取日期** — 从文章标题中提取日期，用于目录命名和后续数据入库

## 使用方式

```bash
python3 <skill-dir>/scripts/download_images.py "<url>" [保存目录]
```

## 目录结构

```
data/images/2026-05-23/文章标题/
├── manifest.json       # URL→路径映射 + 文章元信息
├── img_000.jpg
├── img_001.png
└── ...
```

### manifest.json 格式

```json
{
  "url": "https://mp.weixin.qq.com/s/...",
  "title": "文章标题 2026-05-23",
  "article_date": "2026-05-23",
  "fetched_at": "2026-05-23T10:00:00",
  "images": [
    {
      "url": "https://mmbiz.qpic.cn/...",
      "local_path": "/abs/path/img_000.jpg",
      "analyzed": false
    }
  ]
}
```

- `article_date` — 从标题自动提取的日期（YYYY-MM-DD），提取不到则为空
- `analyzed` — 标记该图片是否已被 AI 工具分析过，由 `save-valuation` 技能更新

## 输出

下载完成后向用户报告：
- 文章标题
- 文章日期（如从标题中提取到）
- 图片数量
- 保存位置
- manifest 路径（供后续 AI 分析工具使用）

## 与其他技能的关系

1. 本技能只负责**下载**，不调用任何 AI 模型
2. 下载后，用户使用其他 AI 工具（如 Qclaw）分析图片内容
3. 分析完成后，由 `save-valuation` 技能把识别结果存入数据库
4. `valuation-trends` 技能读取数据库，提供查询和趋势图

## 依赖

- Python 3.10+
- `playwright`（需 `playwright install chromium`）
- `aiohttp`
