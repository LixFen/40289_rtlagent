"""HTTP 服务的稳定启动入口：python -m serve.api。"""
import os
import signal

from serve.http.server import SubmissionServer
from serve.credentials import load_token


def main():
    """读取服务配置，创建 loopback 服务，并负责最终关闭。"""
    load_token()
    port = int(os.environ.get('PORT','7860'))
    if not 1024 <= port <= 65535:
        raise ValueError('PORT must be within 1024..65535')
    server = SubmissionServer(("127.0.0.1", port))
    def stop(*_):
        raise SystemExit(0)
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()


if __name__ == "__main__":
    main()
