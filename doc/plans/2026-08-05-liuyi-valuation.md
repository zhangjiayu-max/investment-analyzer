# 六亿估值 — 新增估值来源实现方案

## 背景
- 现有估值来源：雷牛牛（单个估值图）、螺丝钉估值（多指数表格）
- 新增：六亿估值（多指数表格，格式与螺丝钉一致，字段：PE/PB/股息率/ROE/估值状态）

## 实现范围

### 1. 后端 — 解析器
- 文件：`backend/services/content/image_parser.py`
- 新增 `LIUYI_PARSE_PROMPT` + `LIUYI_PARSE_PROMPT_CROP`（与 DD 版类似，仅改来源描述）
- 新增 `LiuyiImageParser` 类，继承 `DDImageParser`，仅换 prompt 和 parse_type 标识
- 导出 `liuyi_parser` 实例

### 2. 后端 — 数据库
- 文件：`backend/db/__init__.py`（init_db 建表）
- 新增 `liuyi_valuations` 表（结构同 `dd_valuations`）
- 文件：`backend/db/valuations.py`
- 新增 CRUD：`save_liuyi_valuation` / `list_liuyi_valuations` / `get_liuyi_valuation` / `get_liuyi_parsed_image_paths`
- 文件：`backend/db/__init__.py`（导出）
- 从 `db.valuations` 重导出新增的 CRUD 函数

### 3. 后端 — 路由
- 文件：`backend/routers/market/valuation.py`
- 新增 `LIUYI_IMAGES_DIR` 引用
- 新增 `/parse-liuyi`（同步）、`/parse-liuyi-async`（异步）、`/parse-liuyi-batch-async`（批量异步）
- 新增 `/liuyi/list`（列表）、`/liuyi/{liuyi_id}`（详情）、`/liuyi/indexes`（指数列表）
- 新增 `ParseLiuyiRequest` 模型（复用 `ParseDDRequest` 结构）

### 4. 后端 — 异步任务
- 文件：`backend/scripts/dd_parse_worker.py`
- 扩展 `run_dd_parse` 支持 `parse_type="liuyi"`
- 新增 `_resolve_image_path` 支持 `LIUYI_IMAGES_DIR`

### 5. 后端 — 配置
- 文件：`backend/config.py`
- 新增 `LIUYI_IMAGES_DIR = ROOT / "data" / "liuyi_images"`

### 6. 前端 — API
- 文件：`frontend/src/api/index.js`
- 新增：`parseLiuyiImage` / `parseLiuyiImageAsync` / `parseLiuyiBatchAsync` / `getLiuyiParseTask` / `pollLiuyiParseTask` / `listLiuyiValuations` / `getLiuyiValuation`

### 7. 前端 — ImageGallery.vue
- Tab 新增 "六亿估值" 页签（与螺丝钉 Tab 并列）
- 图片类型判断：`img.path.includes('liuyi_images') || img.name.includes('六亿') || img.name.includes('liuyi')`
- 上传/解析/删除逻辑复制螺丝钉的 DD 模式，调用六亿估值 API

### 8. 前端 — ValuationHistory.vue
- 新增 "六亿估值" 外层 Tab（与 "指数估值"、"螺丝钉估值" 并列）
- 实现 `liuyiRecords` / `liuyiIndexList` / `loadLiuyiIndexList` 等数据展示
- 展示格式与螺丝钉估值一致（多指数表格 + 搜索/排序）

## 不涉及
- 图片静态服务：`/static/liuyi_images/` 由 FastAPI 的 `StaticFiles` mount 自动提供
- 六亿估值数据不写入 `index_valuations` 表（暂不参与统一估值查询，后续可扩展）

## 目录结构
- `data/liuyi_images/` — 六亿估值图片存储目录（需手动创建）