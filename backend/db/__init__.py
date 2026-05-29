"""SQLite 数据层 — 从各子模块重导出，保持 `from db import xxx` 向后兼容。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from db._conn import DB_PATH, _get_conn, _row_to_dict
from db._utils import (
    _add_column_if_not_exists,
    _fix_unique_constraint,
    _fix_holdings_unique_constraint,
    _migrate_old_schema,
    _extract_url_id,
)

# 估值数据 — 从 db.valuations 重导出
from db.valuations import (
    save_valuation, get_valuation_history, get_latest_valuation,
    list_valuation_indexes, list_index_freshness, search_indexes_by_keyword,
    get_index_info, save_index_info, get_valuation_by_image,
)

# 任务 CRUD
from db.tasks import create_task, update_task, get_task, list_tasks, delete_task

# Token 用量 + 性能监控
from db.token_usage import (
    list_token_usage, count_token_usage, get_today_token_total,
    get_token_usage_summary, get_token_budget_info, get_token_usage_by_caller,
    get_token_usage_daily, get_performance_stats, get_performance_by_agent,
)

# 推荐 + 反馈 + 用户画像
from db.dashboard import (
    save_recommendations, list_recommendations, verify_recommendation,
    auto_verify_pending_recommendations, save_recommendation_feedback,
    list_recommendation_feedback, get_recommendation_feedback_stats,
    save_llm_feedback, list_llm_feedback, get_user_profile,
    update_user_profile, increment_feedback_count,
)

# Agent 系统
from db.agents import (
    _init_preset_agents, list_agents, get_agent, create_agent, update_agent,
    delete_agent, save_prompt_version, list_prompt_versions, get_prompt_version,
    create_agent_run, get_agent_runs,
)

# 对话 + 消息 + 摘要
from db.conversations import (
    list_conversations, get_conversation, create_conversation, update_conversation,
    delete_conversation, get_messages, create_message, update_message_metadata,
    update_message_content_and_metadata, get_conversation_summary, save_conversation_summary,
)

# 评测集
from db.eval import (
    init_eval_tables, create_eval_case, list_eval_cases, get_eval_case,
    update_eval_case, delete_eval_case, create_eval_run, list_eval_runs,
    get_eval_stats, get_eval_run_detail,
)

# 文章 + 分析记录 + 作者文章 + 链接文章
from db.articles import (
    sync_articles, list_articles, get_article, get_article_by_seq, get_article_by_url,
    create_article, update_article, create_analysis_record, update_analysis_record,
    list_all_analysis_records, get_analysis_records, get_analysis_record,
    create_author_article, update_author_article, get_author_article_by_url,
    list_author_articles, get_author_article, delete_author_article, count_author_articles,
    create_linked_article, list_linked_articles, get_linked_article, update_linked_article_file,
    delete_linked_article, update_linked_article_embed_status, save_document_chunks,
    get_document_chunks,
)

# 持仓管理全领域
from db.portfolio import (
    create_holding, get_holding, list_holdings, update_holding, delete_holding,
    get_portfolio_summary, get_cash_balance, add_cash, set_cash_balance,
    accrue_cash_interest, save_rebalance_config, get_active_rebalance_config,
    list_rebalance_configs, get_rebalance_config_by_id, rollback_rebalance_config,
    create_transaction, list_transactions, _recalculate_holding, confirm_transaction,
    settle_transaction, delete_transaction, fetch_fund_nav, get_fund_nav_history,
    refresh_holding_price, refresh_all_fund_prices, lookup_fund_info, classify_bond_type,
    classify_fund_category, get_fund_holdings, create_alert, list_alerts,
    get_unread_alert_count, mark_alert_read, delete_alert, add_transaction_tag,
    remove_transaction_tag, get_transaction_tags, get_portfolio_diversification,
    get_transaction_summary, clear_all_portfolio_data, create_portfolio_analysis_record,
    list_portfolio_analysis_records, get_portfolio_analysis_record,
    update_analysis_feedback, list_bad_cases, list_all_bad_cases,
    delete_portfolio_analysis_record, save_analysis_cache, get_analysis_cache,
    get_cached_fund_holdings, get_portfolio_penetration,
)

# AI 分析 Agent + 历史 + Prompt 常量
from db.analysis import (
    _init_analysis_tables, list_analysis_agents, get_analysis_agent,
    update_analysis_agent, create_analysis_history, list_analysis_history,
    get_analysis_history_item, delete_analysis_history,
    DEFAULT_MARKET_ANALYST_PROMPT, DEFAULT_DIVERSIFICATION_PROMPT,
    DEFAULT_PANORAMA_PROMPT, DEFAULT_FUND_DEEP_DIVE_PROMPT,
    DEFAULT_TRADE_REVIEW_PROMPT, DEFAULT_WHATIF_PROMPT,
    DEFAULT_HOTSPOTS_PROMPT, DEFAULT_BOND_PROMPT,
    DEFAULT_INDEX_DEEP_ANALYSIS_PROMPT,
)

# 债券知识库 + 投资策略
from db.bond_knowledge import (
    seed_bond_knowledge, get_bond_market_data, save_bond_market_data,
    seed_investment_strategy_knowledge,
)


def init_db():
    """建表，启动时调用。各子模块的 init_tables(conn) 负责创建自己的表。"""
    conn = _get_conn()

    # ── 指数估值表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)

    # 迁移旧表数据（pe_ttm/pb 分列 → metric_type 统一字段）
    try:
        conn.execute("SELECT pe_ttm FROM index_valuations LIMIT 1")
        # 旧表存在，迁移
        _migrate_old_schema(conn)
    except sqlite3.OperationalError:
        pass  # 已经是新表

    # 修复 UNIQUE 约束：确保包含 metric_type（处理已存在但约束不对的表）
    _fix_unique_constraint(conn)

    # ── 任务表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            publish_time TEXT,
            content_text TEXT,
            codes_found TEXT,
            market_data TEXT,
            llm_analysis TEXT,
            images_dir TEXT,
            local_images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── 文章管理表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seq INTEGER,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            publish_time TEXT,
            status TEXT DEFAULT 'pending',
            images_dir TEXT,
            manifest_path TEXT,
            image_count INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER REFERENCES articles(id),
            image_index INTEGER,
            image_path TEXT UNIQUE,
            image_url TEXT,
            index_code TEXT,
            index_name TEXT,
            metric_type TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            raw_response TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── Agent 系统表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            system_prompt TEXT NOT NULL,
            knowledge_scope TEXT,
            icon TEXT DEFAULT 'robot',
            is_preset INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '新对话',
            agent_id INTEGER REFERENCES agents(id),
            context_data TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS author_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            author TEXT DEFAULT '研究员雷牛牛',
            publish_time TEXT,
            summary TEXT,
            content_text TEXT,
            content_html TEXT,
            article_type TEXT,
            tags TEXT,
            read_count INTEGER,
            like_count INTEGER,
            images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "author_articles", "images", "TEXT")
    _add_column_if_not_exists(conn, "author_articles", "content_html", "TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS linked_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT DEFAULT '',
            file_path TEXT,
            file_size INTEGER,
            file_type TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "linked_articles", "file_path", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "file_size", "INTEGER")
    _add_column_if_not_exists(conn, "linked_articles", "file_type", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "embed_status", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(conn, "linked_articles", "chunks_count", "INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (article_id) REFERENCES linked_articles(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            agent_key TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            query TEXT,
            result TEXT,
            tool_calls TEXT,
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_conv ON agent_runs(conversation_id)")

    # ── 持仓管理表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            index_code TEXT,
            index_name TEXT,
            shares REAL DEFAULT 0,
            cost_price REAL DEFAULT 0,
            current_price REAL,
            total_cost REAL DEFAULT 0,
            current_value REAL,
            profit_loss REAL,
            profit_rate REAL,
            buy_date TEXT,
            last_update TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, fund_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_user ON portfolio_holdings(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_index ON portfolio_holdings(index_code)")
    _add_column_if_not_exists(conn, "portfolio_holdings", "price_updated_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_holdings", "account", "TEXT DEFAULT '默认账户'")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_change_pct", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_profit", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "fund_category", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "portfolio_holdings", "has_base_position", "INTEGER DEFAULT 0")
    # 回填已有持仓的 fund_category
    rows = conn.execute(
        "SELECT id, fund_name FROM portfolio_holdings WHERE fund_category IS NULL OR fund_category = ''"
    ).fetchall()
    for r in rows:
        cat = classify_fund_category(r["fund_name"])
        if cat != "equity":  # 默认就是 equity 类型，只回填非默认的
            conn.execute("UPDATE portfolio_holdings SET fund_category = ? WHERE id = ?", (cat, r["id"]))
    _fix_holdings_unique_constraint(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER REFERENCES portfolio_holdings(id) ON DELETE CASCADE,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            shares REAL,
            price REAL,
            transaction_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_holding ON portfolio_transactions(holding_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_fund ON portfolio_transactions(fund_code)")
    _add_column_if_not_exists(conn, "portfolio_transactions", "status", "TEXT DEFAULT 'confirmed'")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_shares", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_amount", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "confirmed_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "settled_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "transaction_time", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "expected_confirm_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "is_system", "INTEGER DEFAULT 0")

    _add_column_if_not_exists(conn, "portfolio_cash", "last_interest_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_cash", "today_interest", "REAL DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT NOT NULL,
            content TEXT,
            related_fund_code TEXT,
            related_fund_name TEXT,
            source TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON portfolio_alerts(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_read ON portfolio_alerts(is_read)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_cash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default' UNIQUE,
            balance REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER REFERENCES portfolio_transactions(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_tags_tx ON transaction_tags(transaction_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            analysis_type TEXT NOT NULL,
            summary TEXT,
            input_data TEXT,
            result_data TEXT,
            token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_user ON portfolio_analysis_records(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_type ON portfolio_analysis_records(analysis_type)")
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN agent_id INTEGER DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback_note TEXT DEFAULT NULL")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rebalance_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            strategy TEXT NOT NULL,
            config_json TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_user ON rebalance_config(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_active ON rebalance_config(is_active)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL UNIQUE,
            index_name TEXT,
            info TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL DEFAULT 'conversation',
            system_prompt TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apv_agent ON agent_prompt_versions(agent_id, agent_type)")

    # ── Token 用量统计表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            caller TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "token_usage", "caller", "TEXT DEFAULT ''")

    # 初始化预设 Agent
    _init_preset_agents(conn)

    # 初始化评测集表
    init_eval_tables(conn)

    # Skill 文档表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bond_market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type TEXT NOT NULL,
            content TEXT NOT NULL,
            snapshot_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.commit()
    conn.close()

    # 初始化 AI 分析表
    _init_analysis_tables()
