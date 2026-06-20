#!/bin/bash
# Obsidian 快速捕获脚本
# 用法: ./obsidian-quick-capture.sh "你的想法内容"
# 用法: ./obsidian-quick-capture.sh -t book "读书笔记内容"

VAULT="${OBSIDIAN_VAULT:-$HOME/Documents/KnowledgeBase}"
INBOX="$VAULT/00-Inbox"
TEMPLATE="note"
TAGS="quick-capture"

while getopts "t:" opt; do
  case $opt in
    t) TEMPLATE="$OPTARG" ;;
    *) echo "用法: $0 [-t book|article|note] \"内容\""; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

if [ -z "$1" ]; then
  echo "❌ 请提供笔记内容"
  echo "用法: $0 [-t book|article|note] \"你的想法\""
  exit 1
fi

CONTENT="$1"
DATE=$(date +%Y-%m-%d)
TIME=$(date +%H:%M)
# 取内容前 30 个字符作为标题
TITLE=$(echo "$CONTENT" | head -c 30 | tr '/' '-' | tr '\n' ' ')
FILENAME="${DATE}-${TITLE}.md"

cat > "$INBOX/$FILENAME" << EOF
---
title: "$TITLE"
category: $TEMPLATE
tags: [quick-capture]
importance: 5
created: "$DATE"
---

$CONTENT

---
> 创建于 $DATE $TIME，待整理
EOF

echo "✅ 已创建: $INBOX/$FILENAME"
