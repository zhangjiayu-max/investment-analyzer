"""微信图片代理：仅允许必要域名，每次跳转重新验证。"""
from urllib.parse import urlsplit, urljoin
import httpx

_ALLOWED_HOSTS = frozenset({'mmbiz.qpic.cn', 'mmbiz.qlogo.cn', 'mmsns.qpic.cn', 'mp.weixin.qq.com'})
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def validate_image_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in ('https', 'http') or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError('仅支持微信图片域名的标准 HTTP/HTTPS 地址')
    return url


async def fetch_image(url: str) -> tuple[bytes, str]:
    headers = {'Referer':'https://mp.weixin.qq.com/', 'User-Agent':'Mozilla/5.0'}
    async with httpx.AsyncClient(follow_redirects=False, timeout=15, trust_env=False) as client:
        for _ in range(4):
            validate_image_url(url)
            async with client.stream('GET', url, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('图片跳转缺少目标地址')
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').split(';')[0].lower()
                if not content_type.startswith('image/') or content_type == 'image/svg+xml':
                    raise ValueError('目标未返回支持的图片格式')
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_IMAGE_BYTES:
                        raise ValueError('图片超过10MB限制')
                    chunks.append(chunk)
                return b''.join(chunks), content_type
        raise ValueError('图片跳转次数过多')
