-- 资金桶初始化数据
-- 执行: sqlite3 data/valuations.db < scripts/init_goal_buckets.sql

INSERT INTO goal_buckets (name, bucket_type, target_amount, current_amount, target_ratio, risk_level, liquidity_days, priority, notes)
VALUES
('家庭备用金', 'emergency', 50000, 30000, 0.25, 'very_low', 1, 1, '6个月家庭支出'),
('稳健增值', 'stable', 50000, 20000, 0.25, 'low', 90, 2, '1-2年资金，债基为主'),
('长期权益', 'long_term', 100000, 40000, 0.50, 'medium_high', 365, 3, '3年以上，指数基金+主动基金'),
('机会资金', 'opportunity', 20000, 5000, 0.10, 'high', 30, 4, '低估机会加仓专用'),
('学习试错', 'learning', 5000, 2000, 0.02, 'high', 30, 5, '小仓位实验新策略');
