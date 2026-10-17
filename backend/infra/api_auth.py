"""共享部署可选 Bearer 认证；本地个人模式不增加登录流程。"""
import hmac
from starlette.responses import JSONResponse


class ApiAuthMiddleware:
    def __init__(self, app, token=''):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if (self.token and scope['type'] == 'http' and scope.get('method') != 'OPTIONS'
                and path.startswith('/api/') and path not in ('/api/health', '/api/ready')):
            headers = dict(scope.get('headers', []))
            supplied = headers.get(b'authorization', b'').decode('latin1')
            if not hmac.compare_digest(supplied, f'Bearer {self.token}'):
                response = JSONResponse({'code':401,'message':'需要有效的访问凭据','data':None},
                                        status_code=401,headers={'WWW-Authenticate':'Bearer'})
                return await response(scope, receive, send)
        await self.app(scope, receive, send)
