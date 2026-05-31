# 代码审查清单

每次提交代码前，按此清单逐项检查。

## SQL 安全
- [ ] 所有 SQL 使用参数化查询（`?` 占位符），禁止 f-string 拼接用户输入
- [ ] 动态列名/表名用白名单校验，不直接拼入 SQL
- [ ] DELETE/UPDATE 必须有 WHERE 条件，防止误操作全表

## LLM 安全
- [ ] LLM 输出展示给用户前做 HTML 转义（防 XSS）
- [ ] LLM 生成的 JSON 做 schema 校验，不信任任意字段
- [ ] 工具调用参数做类型检查和范围校验
- [ ] 用户输入传给 LLM 前做长度限制

## 金融严谨性
- [ ] 估值阈值从数据库 `analysis_agents` 或配置表读取
- [ ] 计算公式抽成独立函数，参数可配置
- [ ] 展示的数据有来源标识和更新时间
- [ ] 投资建议附带风险提示

## 后端质量
- [ ] 异常处理：外部调用（LLM/akshare/MCP）必须 try-catch + 降级
- [ ] 超时控制：HTTP 请求设 timeout，长时间任务设 cancel_event
- [ ] 日志：关键操作有 logging，错误包含足够上下文
- [ ] 数据库连接：用完 close()，异常时也要 close（或用 context manager）

## 前端质量
- [ ] 操作类按钮用 `ConfirmDialog` 二次确认
- [ ] LLM 生成内容有点赞/点踩反馈按钮
- [ ] 样式用 CSS 变量，不硬编码颜色/间距
- [ ] 加载状态和错误状态都有处理

## RAG 质量
- [ ] 新增知识类型要配置 `_FRESHNESS_POLICY` 时效
- [ ] 索引更新要幂等（先删旧再插入）
- [ ] 检索结果要做 freshness 过滤

## 验证
- [ ] 改后端：确认 `uvicorn --reload` 无报错
- [ ] 改前端：`cd frontend && npm run build:deploy`
- [ ] 改数据库：检查 `init_db()` 和 ALTER TABLE 兼容性
- [ ] 改 prompt：用历史问题重新生成，对比输出质量
