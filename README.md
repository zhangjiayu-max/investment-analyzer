# 投资分析助手

一个基于 AI 的投资分析工具，支持粘贴微信公众号链接自动解读。

## 功能

- **公众号解读**：粘贴链接，AI 自动提取文章核心观点
- **标的识别**：从文章中自动识别股票/基金代码
- **行情分析**：拉取实时行情数据，PE/PB 估值分析
- **K 线图**：自动生成交互式 K 线图
- **AI 建议**：结合文章内容和行情数据给出投资建议

## 快速开始

### 1. 安装后端依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key
```

### 3. 启动后端

```bash
python app.py
# 或
uvicorn app:app --reload
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

然后打开 http://localhost:5173

## 技术栈

| 层 | 方案 |
|---|---|
| 后端 | FastAPI + akshare |
| 前端 | Vue 3 + Vite + Tailwind CSS + ECharts |
| 大模型 | DeepSeek API（可换成其他国内模型） |

## 目录结构

```
investment-analyzer/
├── app.py                  # FastAPI 后端入口
├── config.py               # 配置管理
├── llm_service.py          # 大模型封装
├── article_reader.py       # 公众号文章抓取
├── market_data.py          # 行情数据获取
├── valuation.py            # 估值分析
├── frontend/               # Vue 3 前端
│   ├── src/
│   │   ├── api/            # API 封装
│   │   ├── components/     # Vue 组件
│   │   └── views/          # 页面
│   └── ...
├── .env.example            # 环境变量示例
├── requirements.txt
└── README.md
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analyze` | 分析公众号文章 |
| POST | `/api/stock` | 单只股票/基金分析 |
| GET | `/api/chart/{symbol}` | 获取 K 线图数据 |
| POST | `/api/chat` | 自由问答 |
| GET | `/api/health` | 健康检查 |

启动后访问 http://localhost:8000/docs 查看完整 API 文档。
