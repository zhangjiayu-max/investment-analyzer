#!/bin/bash
# 重启后端服务 — 始终从项目根目录执行
cd "$(dirname "$0")/../backend"
lsof -ti:8000 | xargs kill -9 2>/dev/null
sleep 1
nohup python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload > /tmp/app.log 2>&1 &
sleep 3
if curl -s http://localhost:8000/api/dashboard > /dev/null 2>&1; then
  echo "✅ 后端启动成功"
else
  echo "❌ 后端启动失败"
  tail -5 /tmp/app.log
fi
