"""HTTP 鉴权与路由；耗时求解交给任务队列。"""
import hmac
from http.server import BaseHTTPRequestHandler
import json
import os
import time

from serve.health import health
from serve.http.contract import validate_payload

class Handler(BaseHTTPRequestHandler):
    """把认证后的 HTTP 请求转换为任务提交或公开状态查询。"""

    def log_message(self, *_):
        """访问日志由外层服务收集，避免将请求信息写入标准错误。"""
        pass

    def respond(self, status, body):
        """返回 JSON；客户端断开不会影响已入队任务。"""
        data = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def authorized(self):
        """核对 Bearer 令牌，并完整返回未授权响应。"""
        token = os.environ.get("FPGACHINA_TOKEN", "")
        if not token or not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
            # 消费小型 POST 请求体，避免 Windows 关闭带未读数据的连接时用 RST 覆盖 401。
            if self.command == 'POST':
                try:
                    length = int(self.headers.get('Content-Length','0'))
                    if 0 < length <= 262144:
                        self.connection.settimeout(0.1)
                        self.rfile.read(length)
                except (OSError,ValueError):
                    pass
            self.respond(401, dict(error="unauthorized"))
            return False
        return True

    def do_GET(self):
        """读取健康状态或任务快照，不等待求解。"""
        if not self.authorized():
            return
        if self.path == '/v1/health':
            return self.respond(200,health())
        if self.path.startswith('/v1/jobs/'):
            job = self.server.jobs.get(self.path.removeprefix('/v1/jobs/'))
            return self.respond(200,job) if job else self.respond(404,{'error':'unknown_job'})
        self.respond(404,{})

    def do_POST(self):
        """校验公开字段后立即返回 202 和任务快照。"""
        started = time.monotonic()
        if not self.authorized():
            return
        if self.path != "/v1/solve":
            return self.respond(404, {})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                raise ValueError()
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(length))
            validate_payload(payload)
        except (ValueError, OSError):
            return self.respond(400, dict(error="invalid_request"))
        self.respond(202,self.server.jobs.submit(payload,started))
