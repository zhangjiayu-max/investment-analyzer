# 对话图片上传与解析 — 设计稿

> 日期：2026-07-04
> 状态：待实施

## 1. 目标

用户在对话中直接上传图片（持仓截图、估值图、K线图、文章截图等），系统自动解析图片内容，将解析结果作为上下文透传给多Agent编排，让AI能"看到"图片并给出针对性建议。

## 2. 现状

| 能力 | 状态 |
|------|------|
| 图片上传API | 有 `/api/dd-images/upload` 和 `/api/valuation-images/upload`，但仅限估值图，未接入对话 |
| 图片解析 | 有 `image_parser._call_vision()` + `llm_service.analyze_image()`，支持 Ollama/MiMo 视觉模型 |
| 对话消息 | `messages` 表只有 `content`(文本) + `metadata`(JSON)，无附件字段 |
| 前端输入框 | `ChatInput.vue` 纯文本 textarea，无文件上传 |
| 对话编排 | `orchestrate_stream()` 只接收 `query: str`，不支持图片 |

**核心缺口**：对话流程完全没有图片入口。

## 3. 设计决策

### 3.1 是否新增表？

**不需要新表。** 原因：

1. `messages.metadata` 已是 JSON 字段，可存图片引用
2. 图片文件本身存磁盘（复用 `data/chat_images/` 目录），DB 只存路径
3. 图片解析结果存 `messages.metadata.image_analysis`，随消息一起检索
4. 避免额外 JOIN，查询开销零增加

**但需要新增一个目录**：`data/chat_images/` — 对话图片专用存储

### 3.2 数据流

```
用户选图 → 前端上传 → 后端保存文件 → 异步调视觉模型解析
                                    ↓
解析结果(JSON) → 存入 user message 的 metadata.image_analysis
                                    ↓
编排入口 → 从 metadata 取解析结果 → 拼入 query 上下文
                                    ↓
多Agent专家 → 收到 "用户上传了图片，内容如下：{解析结果}" → 正常分析
```

### 3.3 关键设计选择

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 解析时机 | 上传时立即解析（非对话时） | 用户上传后马上看到解析结果，体验好；对话时零延迟 |
| 解析失败处理 | 允许发送，metadata 标记 `parse_failed` | 不阻塞用户，AI仍可看文件名猜测 |
| 图片持久化 | 保存到磁盘 + DB存路径 | 可回溯、可重新解析 |
| 透传方式 | 拼入query文本 | 不改orchestrator接口签名，最小侵入 |
| 支持格式 | jpg/jpeg/png/webp/gif | 覆盖99%场景 |
| 大小限制 | 10MB | 足够覆盖高清截图 |
| 多图支持 | 单次最多3张 | 避免token爆炸 |

## 4. 技术方案

### 4.1 后端

#### 4.1.1 新增文件：`backend/routers/chat_images.py`

```python
"""对话图片上传与解析路由 — /api/chat-images/*"""

from pathlib import Path
from datetime import datetime
import json, logging, uuid, base64

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from config import ROOT
from llm_service import analyze_image

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat-images"])

CHAT_IMAGES_DIR = ROOT / "data" / "chat_images"
CHAT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

MAX_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class UploadResponse(BaseModel):
    image_id: str
    url: str
    thumbnail_url: str | None = None
    parse_status: str  # "done" | "failed" | "skipped"
    parse_result: dict | None = None


@router.post("/api/chat-images/upload", response_model=UploadResponse)
async def upload_chat_image(file: UploadFile = File(...)):
    """上传对话图片，立即解析，返回解析结果。"""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的图片格式: {ext}")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "图片大小不能超过10MB")

    # 存储文件
    image_id = uuid.uuid4().hex[:12]
    date_dir = datetime.now().strftime("%Y-%m-%d")
    save_dir = CHAT_IMAGES_DIR / date_dir
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{image_id}{ext}"
    save_path = save_dir / filename
    save_path.write_bytes(content)

    relative_path = f"{date_dir}/{filename}"
    url = f"/static/chat_images/{relative_path}"

    # 立即解析（同步，用户等待结果）
    parse_status = "done"
    parse_result = None
    try:
        parse_result = analyze_image(str(save_path))
    except Exception as e:
        logger.warning(f"图片解析失败 {image_id}: {e}")
        parse_status = "failed"
        parse_result = {"error": str(e)}

    return UploadResponse(
        image_id=image_id,
        url=url,
        parse_status=parse_status,
        parse_result=parse_result,
    )
```

#### 4.1.2 修改 `backend/app.py` — 注册路由 + 静态文件

```python
# 新增导入
from routers.chat_images import router as chat_images_router

# 注册路由
app.include_router(chat_images_router)

# 新增静态文件挂载
CHAT_IMAGES_DIR = ROOT / "data" / "chat_images"
app.mount("/static/chat_images", StaticFiles(directory=str(CHAT_IMAGES_DIR)), name="chat_images")
```

#### 4.1.3 修改 `backend/routers/conversations.py` — 透传图片解析结果

在 `SendMessageRequest` 中新增可选字段：

```python
class SendMessageRequest(BaseModel):
    content: str
    target_specialists: list[str] = []
    images: list[dict] = []  # 新增：[{"image_id": "xxx", "url": "/static/...", "parse_result": {...}}]
```

在 `send_message_stream` 和 `send_message_api` 中，存储用户消息时将图片信息写入 metadata：

```python
# 存储用户消息时，附带图片metadata
user_metadata = {}
if req.images:
    user_metadata["images"] = req.images

user_msg_id = create_message(
    conv_id, "user", req.content,
    metadata=json.dumps(user_metadata, ensure_ascii=False) if user_metadata else None
)
```

在调用 orchestrator 前，将图片解析结果拼入 query：

```python
# 构建增强query：图片解析结果透传给编排
effective_query = req.content
if req.images:
    image_context_parts = []
    for img in req.images:
        if img.get("parse_result") and not img["parse_result"].get("error"):
            pr = img["parse_result"]
            image_context_parts.append(
                f"[用户上传图片 {img.get('image_id','')}] "
                f"类型: {pr.get('image_type','未知')}, "
                f"摘要: {pr.get('summary','')}"
            )
    if image_context_parts:
        image_context = "\n".join(image_context_parts)
        effective_query = f"{req.content}\n\n[图片上下文]\n{image_context}"
```

然后后续所有用 `req.content` 的地方改用 `effective_query`（RAG检索、orchestrator调用等）。

#### 4.1.4 修改 `backend/db/conversations.py` — 无需改动

`create_message` 已支持 metadata 参数，`_load_metadata` / `_dump_metadata` 已处理 JSON 序列化。

### 4.2 前端

#### 4.2.1 修改 `ChatInput.vue` — 新增图片上传入口

**UI变更**：
- 输入框左侧新增 📎 图片按钮
- 点击触发 `<input type="file" accept="image/*" multiple>` (最多3张)
- 支持粘贴图片（`paste` 事件监听）
- 上传中显示 loading 状态
- 上传完成后在输入框上方显示缩略图列表（可删除）

**数据流**：
```javascript
// 新增 ref
const attachedImages = ref([])  // [{image_id, url, parse_result, parse_status}]

// 上传函数
async function uploadImage(file) {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/chat-images/upload', { method: 'POST', body: formData })
  const data = await res.json()
  attachedImages.value.push(data)
}

// 发送时携带图片
function handleSend() {
  emit('send', {
    images: attachedImages.value.map(i => ({
      image_id: i.image_id,
      url: i.url,
      parse_result: i.parse_result
    }))
  })
  attachedImages.value = []  // 清空
}
```

**emit 签名变更**：
- 原来：`emit('send')` 
- 改为：`emit('send', { images })` 或保持 `emit('send')` + 通过 `defineModel` 或 props 传递图片

**最小侵入方案**：新增 `emit('images-attached', attachedImages)` 事件，ChatView 监听后在 send 时合并到请求体。

#### 4.2.2 修改 `ChatView.vue` — 处理图片附件

```javascript
// 新增 ref
const pendingImages = ref([])

// 监听 ChatInput 的图片附件事件
function onImagesAttached(images) {
  pendingImages.value = images
}

// 发送消息时携带图片
async function sendMessage() {
  const body = {
    content: inputText.value,
    target_specialists: selectedSpecialists.value,
    images: pendingImages.value  // 新增
  }
  // ... fetch /api/conversations/{id}/stream
  pendingImages.value = []  // 清空
}
```

#### 4.2.3 修改 `ChatMessage.vue` — 展示用户消息中的图片

在用户消息气泡中，如果 `metadata.images` 存在，渲染缩略图列表：

```html
<div v-if="message.metadata?.images?.length" class="message-images">
  <img v-for="img in message.metadata.images" 
       :key="img.image_id" 
       :src="img.url" 
       class="message-image-thumb"
       @click="previewImage(img.url)" />
</div>
```

### 4.3 数据结构

#### 4.3.1 用户消息 metadata（存DB）

```json
{
  "images": [
    {
      "image_id": "a1b2c3d4e5f6",
      "url": "/static/chat_images/2026-07-04/a1b2c3d4e5f6.png",
      "parse_status": "done",
      "parse_result": {
        "image_type": "估值图",
        "title": "中证医药50",
        "data": {
          "index_name": "中证医药50",
          "current_value": "5200.3",
          "percentile": "15%"
        },
        "summary": "中证医药50当前百分位15%，处于低估区域"
      }
    }
  ]
}
```

#### 4.3.2 透传给编排的增强query

```
帮我分析下这个基金要不要加仓

[图片上下文]
[用户上传图片 a1b2c3d4e5f6] 类型: 估值图, 摘要: 中证医药50当前百分位15%，处于低估区域
```

## 5. 边界处理

| 场景 | 处理 |
|------|------|
| 视觉模型不可用 | `parse_status="failed"`，允许发送，AI只看到文件名 |
| 图片过大 | 前端压缩 + 后端拒绝 >10MB |
| 非图片文件 | 后端校验扩展名，拒绝 |
| 解析超时 | 30秒超时，标记failed |
| 重复上传同一张 | 允许，每张独立image_id |
| 对话历史中图片 | 从metadata读取，展示缩略图 |
| 移动端 | 图片按钮适配触摸，支持相机拍照 |

## 6. 实施计划

| 阶段 | 内容 | 预计改动 |
|------|------|---------|
| P1 | 后端：上传API + 解析 + 路由注册 | 新增1文件，改2文件 |
| P2 | 后端：对话透传（SendMessageRequest + query增强） | 改1文件 |
| P3 | 前端：ChatInput上传入口 + 缩略图 + 粘贴 | 改1文件 |
| P4 | 前端：ChatView发送合并 + ChatMessage展示 | 改2文件 |
| P5 | 静态文件挂载 + 目录创建 | 改1文件 |

**总改动量**：新增1文件，修改6文件，约500行代码。

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 视觉模型超时 | 中 | 用户等待 | 30秒超时 + 允许跳过 |
| 解析结果不准确 | 中 | AI建议偏差 | 解析结果标注为"图片自动解析"，AI需结合用户文字判断 |
| 磁盘空间 | 低 | 存储满 | 定期清理90天前的对话图片 |
| 图片含敏感信息 | 低 | 隐私泄露 | 图片存本地不外传，视觉模型走本地Ollama |

## 8. 未来扩展

- 支持拖拽上传
- 图片OCR（截图中文字提取）
- 多图关联分析（如对比两张估值图）
- 图片标注（用户在图上画圈标记，AI关注标记区域）
