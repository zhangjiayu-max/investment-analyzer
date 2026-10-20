"""风险生命周期：通知已读不改变风险事实，只有成功完整巡检才能自动恢复。"""
import json
import math
from datetime import datetime, timedelta

from db._conn import _get_conn

ACTIVE_STATES = ('active', 'in_progress', 'recurred')
_SEVERITY = {'info': 0, 'warning': 1, 'danger': 2}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def init_alert_lifecycle(conn):
    """在 portfolio_alerts 既有 schema 迁移后调用；不提交调用者事务。"""
    columns = {r[1] for r in conn.execute('PRAGMA table_info(portfolio_alerts)')}
    for name, declaration in {
        'risk_status': 'TEXT', 'first_detected_at': 'TEXT', 'last_confirmed_at': 'TEXT',
        'resolved_at': 'TEXT', 'fact_date': 'TEXT', 'recovery_condition': 'TEXT',
        'evidence': 'TEXT', 'revision': 'INTEGER DEFAULT 1', 'episode': 'INTEGER DEFAULT 1',
        'last_notified_at': 'TEXT', 'action_reason': 'TEXT', 'review_date': 'TEXT',
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE portfolio_alerts ADD COLUMN {name} {declaration}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_alerts_lifecycle ON portfolio_alerts(user_id,risk_status,severity)')
    conn.execute('''CREATE TABLE IF NOT EXISTS alert_lifecycle_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER NOT NULL,
        event_type TEXT NOT NULL, evidence TEXT, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS alert_scan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'default',
        source TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)''')


def _event(conn, alert_id, event_type, evidence=None):
    conn.execute('INSERT INTO alert_lifecycle_events(alert_id,event_type,evidence,created_at) VALUES(?,?,?,?)',
                 (alert_id, event_type, json.dumps(evidence or {}, ensure_ascii=False), _now()))


def create_alert(alert_type, title, content=None, severity='info', related_fund_code=None,
                 related_fund_name=None, source=None, user_id='default', holding_id=None,
                 *, evidence=None, fact_date=None, recovery_condition=None):
    """同一风险持续更新，恶化/复发/超过一天未提醒时重新未读。返回兼容旧API的ID。"""
    if severity not in _SEVERITY:
        raise ValueError('无效风险级别')
    evidence = evidence or {}
    now = _now()
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''SELECT * FROM portfolio_alerts WHERE user_id=? AND alert_type=?
            AND COALESCE(related_fund_code,'')=? ORDER BY id DESC LIMIT 1''',
            (user_id, alert_type, related_fund_code or '')).fetchone()
        if row:
            old = dict(row)
            old_evidence = json.loads(old.get('evidence') or '{}')
            worsened = _SEVERITY[severity] > _SEVERITY.get(old['severity'], 0)
            value, previous = evidence.get('risk_value'), old_evidence.get('risk_value')
            if isinstance(value, (int, float)) and isinstance(previous, (int, float)):
                # 小幅数值波动不重复打扰；风险值必须由扫描器统一为“越大越危险”。
                worsened = worsened or value > previous + max(1, abs(previous) * .1)
            recurred = old.get('risk_status') == 'resolved'
            review_due = old.get('acknowledged_status') == 'ignored' and old.get('review_date') and old['review_date'] <= now[:10]
            event = 'recurred' if recurred else 'worsened' if worsened else 'review_due' if review_due else 'confirmed'
            reminder = (old.get('last_notified_at') or now) <= (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
            notify = recurred or worsened or reminder or bool(review_due)
            status = 'recurred' if recurred else (old.get('risk_status') or 'active')
            conn.execute('''UPDATE portfolio_alerts SET title=?,content=?,severity=?,source=?,
                related_fund_name=?, holding_id=COALESCE(?,holding_id), risk_status=?,
                first_detected_at=COALESCE(first_detected_at,created_at),last_confirmed_at=?,
                fact_date=COALESCE(?,fact_date),recovery_condition=COALESCE(?,recovery_condition),
                evidence=?,resolved_at=NULL,revision=COALESCE(revision,1)+?,episode=COALESCE(episode,1)+?,
                is_read=CASE WHEN ? THEN 0 ELSE is_read END,
                acknowledged_status=CASE WHEN ? THEN NULL ELSE acknowledged_status END,
                last_notified_at=CASE WHEN ? THEN ? ELSE COALESCE(last_notified_at,?) END WHERE id=?''',
                (title,content,severity,source,related_fund_name,holding_id,status,now,fact_date,
                 recovery_condition,json.dumps(evidence,ensure_ascii=False),int(worsened or recurred),
                 int(recurred),notify,int(worsened or recurred or review_due),notify,now,now,old['id']))
            alert_id = old['id']
            _event(conn, alert_id, event, evidence)
        else:
            if holding_id is None and related_fund_code:
                holding = conn.execute('SELECT id FROM portfolio_holdings WHERE user_id=? AND fund_code=? LIMIT 1',
                                       (user_id, related_fund_code)).fetchone()
                holding_id = holding['id'] if holding else None
            cur = conn.execute('''INSERT INTO portfolio_alerts(user_id,alert_type,title,content,severity,
                related_fund_code,related_fund_name,source,holding_id,risk_status,first_detected_at,
                last_confirmed_at,fact_date,recovery_condition,evidence,last_notified_at)
                VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?)''',
                (user_id,alert_type,title,content,severity,related_fund_code,related_fund_name,source,
                 holding_id,now,now,fact_date,recovery_condition,json.dumps(evidence,ensure_ascii=False),now))
            alert_id = cur.lastrowid
            _event(conn, alert_id, 'detected', evidence)
        conn.commit()
        return alert_id
    finally:
        conn.close()


def _list(conn, user_id='default', limit=50, unread_only=False, active_only=False, offset=0,
          severity=None, alert_type=None):
    conditions, args = ['user_id=?'], [user_id]
    if unread_only:
        conditions.append("is_read=0 AND COALESCE(risk_status,'active')!='resolved'")
    if active_only:
        conditions.append("COALESCE(risk_status,'active') != 'resolved'")
    if severity in ('danger', 'warning', 'info'):
        conditions.append('severity=?'); args.append(severity)
    elif severity == 'danger_warning':
        conditions.append("severity IN ('danger','warning')")
    if alert_type:
        conditions.append('alert_type=?'); args.append(alert_type)
    where = ' AND '.join(conditions)
    groups = f'''SELECT alert_type,COALESCE(related_fund_code,'') AS code,severity,
        COUNT(*) AS cnt,SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) AS unread_count,
        MIN(is_read) AS is_read,MAX(id) AS latest_id,MIN(created_at) AS first_at,
        MAX(COALESCE(last_confirmed_at,created_at)) AS latest_at
        FROM portfolio_alerts WHERE {where} GROUP BY alert_type,COALESCE(related_fund_code,''),severity'''
    total = conn.execute(f'SELECT COUNT(*) FROM ({groups})', args).fetchone()[0]
    rows = conn.execute(f'''SELECT * FROM ({groups}) ORDER BY
        CASE severity WHEN 'danger' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
        latest_at DESC,latest_id DESC LIMIT ? OFFSET ?''', (*args,max(1,min(int(limit),200)),max(0,int(offset)))).fetchall()
    result = []
    for grouped in rows:
        detail = dict(conn.execute('SELECT * FROM portfolio_alerts WHERE id=?', (grouped['latest_id'],)).fetchone())
        detail.update(dict(grouped))
        detail['id'] = detail['latest_id']
        detail['risk_status'] = detail['risk_status'] or 'active'
        detail['evidence'] = json.loads(detail.get('evidence') or '{}')
        detail['reliability'] = calculate_reliability(None)
        result.append(detail)
    _enrich_reliability(conn, result)
    return result,total


def list_alerts(user_id='default', limit=50, unread_only=False, *, active_only=False, offset=0):
    conn = _get_conn()
    try:
        return _list(conn,user_id,limit,unread_only,active_only,offset)[0]
    finally:
        conn.close()


def list_active_alerts_page(user_id='default', limit=50, offset=0, severity=None, alert_type=None,
                            active_only=True, unread_only=False):
    conn = _get_conn()
    try:
        alerts,total = _list(conn,user_id,limit,unread_only,active_only,offset,severity,alert_type)
        active_count = conn.execute("SELECT COUNT(*) FROM portfolio_alerts WHERE user_id=? AND COALESCE(risk_status,'active')!='resolved'",(user_id,)).fetchone()[0]
        return {'alerts':alerts,'total':total,'active_count':active_count,'offset':offset,'limit':min(limit,200),
                'last_scan':get_last_scan(user_id)}
    finally:
        conn.close()


def mark_alert_read(alert_id):
    conn = _get_conn()
    try:
        row = conn.execute('SELECT * FROM portfolio_alerts WHERE id=?',(alert_id,)).fetchone()
        if not row:
            return False
        conn.execute('''UPDATE portfolio_alerts SET is_read=1 WHERE user_id=? AND alert_type=?
            AND COALESCE(related_fund_code,'')=? AND severity=?''',
            (row['user_id'],row['alert_type'],row['related_fund_code'] or '',row['severity']))
        conn.commit()
        return True
    finally:
        conn.close()


def resolve_completed_scan(alert_types, observed_keys, *, source, success, user_id='default', evidence=None):
    """只恢复已完整覆盖的类别；observed_keys 是 (alert_type,fund_code) 集合。"""
    if not success or not alert_types:
        return 0
    observed = {(kind, code or '') for kind,code in observed_keys}
    conn = _get_conn()
    try:
        resolved = 0
        for kind in alert_types:
            rows = conn.execute("SELECT * FROM portfolio_alerts WHERE user_id=? AND source=? AND alert_type=? AND COALESCE(risk_status,'active')!='resolved'",
                                (user_id,source,kind)).fetchall()
            for row in rows:
                if (kind,row['related_fund_code'] or '') in observed:
                    continue
                conn.execute("UPDATE portfolio_alerts SET risk_status='resolved',resolved_at=? WHERE id=?",(_now(),row['id']))
                _event(conn,row['id'],'resolved',evidence or {'condition':'完整巡检确认已低于触发阈值'})
                resolved += 1
        conn.commit()
        return resolved
    finally:
        conn.close()


def update_alert_action(alert_id, status, *, reason=None, review_date=None, user_id='default'):
    """采纳是处理中；忽略保留活动事实并要求原因和复查日期；成交另由交易记录证明。"""
    if status not in ('acknowledged','ignored'):
        raise ValueError('仅支持采纳或忽略')
    if status == 'ignored' and (not reason or not reason.strip() or not review_date):
        raise ValueError('忽略风险必须填写原因与复查日期')
    if review_date:
        review = datetime.strptime(review_date, '%Y-%m-%d')
        if review.date() < datetime.now().date():
            raise ValueError('复查日期不能早于今天')
    conn = _get_conn()
    try:
        row = conn.execute('SELECT * FROM portfolio_alerts WHERE id=? AND user_id=?',(alert_id,user_id)).fetchone()
        if not row:
            return False
        conn.execute('''UPDATE portfolio_alerts SET acknowledged_status=?,action_reason=?,review_date=?,
            risk_status=CASE WHEN risk_status='resolved' THEN 'resolved' WHEN ?='acknowledged' THEN 'in_progress'
            ELSE COALESCE(risk_status,'active') END WHERE id=?''',(status,reason,review_date,status,alert_id))
        _event(conn,alert_id,status,{'reason':reason,'review_date':review_date,'trade_executed':False})
        conn.commit()
        return True
    finally:
        conn.close()


def cleanup_old_alerts(user_id='default', days=14, include_unread=True, auto_ignore_info_days=7):
    """仅清理已恢复的旧通知；活动风险永不因时间或阅读状态被清掉。"""
    conn = _get_conn()
    try:
        predicate = '' if include_unread else ' AND is_read=1'
        cur = conn.execute("DELETE FROM portfolio_alerts WHERE user_id=? AND risk_status='resolved' AND resolved_at < datetime('now','localtime',?)"+predicate,
                           (user_id,f'-{max(0,days)} days'))
        conn.commit()
        return {'deleted':cur.rowcount,'auto_ignored':0}
    finally:
        conn.close()


def get_lifecycle_history(alert_id, user_id='default'):
    conn = _get_conn()
    try:
        return [dict(r) for r in conn.execute('''SELECT e.* FROM alert_lifecycle_events e
            JOIN portfolio_alerts a ON e.alert_id=a.id WHERE e.alert_id=? AND a.user_id=? ORDER BY e.id DESC''',(alert_id,user_id))]
    finally:
        conn.close()


def record_scan(result, source='periodic', user_id='default'):
    conn = _get_conn()
    try:
        conn.execute('INSERT INTO alert_scan_runs(user_id,source,result,created_at) VALUES(?,?,?,?)',
                     (user_id,source,json.dumps(result,ensure_ascii=False),_now()))
        conn.commit()
    finally:
        conn.close()


def get_last_scan(user_id='default'):
    conn = _get_conn()
    try:
        row = conn.execute('SELECT result FROM alert_scan_runs WHERE user_id=? ORDER BY id DESC LIMIT 1',(user_id,)).fetchone()
        return json.loads(row['result']) if row else None
    finally:
        conn.close()


def calculate_reliability(stats):
    """同窗口、按样本加权并展示Wilson区间；样本不足绝不发布高可靠性。"""
    if not stats or not stats.get('total_samples'):
        return {'level':'unknown','label':'证据不足','reason':'尚无同观察窗口的成熟回测样本',
                'samples':0,'win_rate':None,'avg_change':None,'confidence_interval':None}
    n = stats['total_samples']; win_rate = float(stats.get('avg_win_rate') or 0)
    p = min(1,max(0,win_rate/100)); z = 1.96
    center = (p+z*z/(2*n))/(1+z*z/n)
    delta = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    interval = [round((center-delta)*100,1),round((center+delta)*100,1)]
    level,label = ('unknown','样本不足') if n < 30 else ('medium','有限依据')
    return {'level':level,'label':label,'reason':f'成熟样本{n}；95%区间{interval[0]}%–{interval[1]}%；历史命中不代表建议有效',
            'samples':n,'win_rate':round(win_rate,1),'avg_change':stats.get('avg_change'),'confidence_interval':interval}


def _enrich_reliability(conn, alerts):
    try:
        # 仅合并同 horizon_days 的样本，无法确定观察窗口时保留“证据不足”。
        columns = {r[1] for r in conn.execute('PRAGMA table_info(alert_accuracy_stats)')}
        if 'followup_days' not in columns:
            return
        stats = conn.execute('''SELECT alert_type,severity,followup_days,
            SUM(win_rate*sample_count)/NULLIF(SUM(sample_count),0) AS avg_win_rate,
            SUM(sample_count) AS total_samples,SUM(avg_followup_change*sample_count)/NULLIF(SUM(sample_count),0) AS avg_change
            FROM alert_accuracy_stats WHERE week_start>=date('now','localtime','-28 days')
            GROUP BY alert_type,severity,followup_days''').fetchall()
        mapping = {}
        for row in stats:
            key = (row['alert_type'],row['severity'])
            if key not in mapping or row['total_samples']>mapping[key]['total_samples']:
                mapping[key] = dict(row)
        for alert in alerts:
            alert['reliability'] = calculate_reliability(mapping.get((alert['alert_type'],alert['severity'])))
    except Exception:
        # 可选统计不可抹掉风险事实；没有可核对窗口时不作可靠性承诺。
        return


def get_unread_alert_count(user_id='default'):
    conn = _get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM portfolio_alerts WHERE user_id=? AND is_read=0 AND COALESCE(risk_status,'active')!='resolved'",(user_id,)).fetchone()[0]
    finally:
        conn.close()


def delete_resolved_alert(alert_id, user_id='default'):
    """只删除完整恢复的同分组；旧SQL遗留的活动重复行也受保护。"""
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM portfolio_alerts WHERE id=? AND user_id=?',(alert_id,user_id)).fetchone()
        if not row:
            return False
        group = (user_id,row['alert_type'],row['related_fund_code'] or '',row['severity'])
        active = conn.execute("""SELECT id FROM portfolio_alerts WHERE user_id=? AND alert_type=?
            AND COALESCE(related_fund_code,'')=? AND severity=? AND COALESCE(risk_status,'active')!='resolved' LIMIT 1""",group).fetchone()
        if active:
            raise ValueError('活动风险不能删除；待完整巡检确认恢复后再清理')
        conn.execute("""DELETE FROM portfolio_alerts WHERE user_id=? AND alert_type=?
            AND COALESCE(related_fund_code,'')=? AND severity=?""",group)
        conn.commit()
        return True
    finally:
        conn.close()
