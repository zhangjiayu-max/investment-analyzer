"""分析失败的可持久化诊断；不记录提示词、请求头或 URL 凭据。"""
import re
from urllib.parse import urlsplit, urlunsplit


def _safe_attr(value, name, default=None):
    # httpx.RequestError.request 在尚未绑定请求时会抛 RuntimeError。
    try:
        return getattr(value, name, default)
    except Exception:
        return default


def safe_endpoint(value):
    try:
        url = urlsplit(str(value))
        host = url.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        port = f':{url.port}' if url.port else ''
        return urlunsplit((url.scheme, host + port, url.path, '', ''))
    except ValueError:
        return '[invalid URL]'


def safe_error_text(value):
    text = re.sub(r'https?://[^\s\'"<>]+', lambda m: safe_endpoint(m.group()), str(value))
    text = re.sub(r'(?i)\b(bearer|basic)\s+[^\s,;\'"}]+', r'\1 [redacted]', text)
    text = re.sub(r'''(?ix)\b((?:api[_-]?key|authorization|password|token)["']?\s*[=:]\s*)
                       (?:"[^"]*"|'[^']*'|[^\s,;}]+)''',
                  r'\1[redacted]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    return text[:1500]


def build_error_details(error, *, stage='', caller='', trace_id='', model='', endpoint='',
                        elapsed_ms=None, attempts=None):
    causes, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(causes) < 8:
        seen.add(id(current))
        causes.append({'type': type(current).__name__, 'message': safe_error_text(current)})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    text = ' '.join(c['type'] + ' ' + c['message'] for c in causes).lower()
    if 'executioncancelled' in text or 'cancellederror' in text:
        category, summary = 'cancelled', '分析任务已取消。'
    elif 'connectionrefused' in text or 'connection refused' in text:
        category, summary = 'connection_refused', '连接被拒绝，请检查模型服务地址及本机代理是否可用。'
    elif 'proxyerror' in text:
        category, summary = 'proxy_error', '代理连接失败，请检查代理服务及网络配置。'
    elif any(s in text for s in ('name or service not known', 'nodename nor servname', 'getaddrinfo', 'gaierror')):
        category, summary = 'dns_error', '模型服务域名解析失败，请检查 DNS 和网络。'
    elif 'timeout' in text or isinstance(error, TimeoutError):
        category, summary = 'timeout', '请求或执行预算超时，请核对底层异常和耗时。'
    elif any(s in text for s in ('connection reset', 'connectionreset', 'remoteprotocolerror', 'readerror', 'writeerror')):
        category, summary = 'connection_interrupted', '模型连接在传输期间中断，请检查网络、代理及服务端状态。'
    elif 'ssl' in text or 'certificate' in text:
        category, summary = 'tls_error', 'HTTPS 证书或 TLS 连接失败。'
    elif 'connectionerror' in text or 'connecterror' in text:
        category, summary = 'connection_error', '模型连接失败；具体原因以已保存的底层异常为准。'
    elif isinstance(error, ValueError):
        category, summary = 'invalid_response', '分析响应或数据格式无效。'
    else:
        category, summary = 'execution_error', '分析执行失败，请查看异常类型和底层原因。'
    request = _safe_attr(error, 'request')
    request_url = _safe_attr(request, 'url', '')
    response = _safe_attr(error, 'response')
    details = {'category': category, 'summary': summary, 'stage': stage, 'caller': caller,
               'trace_id': trace_id, 'model': model, 'endpoint': safe_endpoint(request_url or endpoint),
               'causes': causes, 'elapsed_ms': elapsed_ms,
               'status_code': _safe_attr(error, 'status_code'),
               'request_id': safe_error_text(_safe_attr(error, 'request_id', '') or ''),
               'attempt_count': len(attempts or []), 'attempts': attempts or []}
    if response is not None and not details['request_id']:
        details['request_id'] = safe_error_text(response.headers.get('x-request-id', ''))
    return details
