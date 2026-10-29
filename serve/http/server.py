"""装配 HTTP 处理器、任务队列及 Linux 子进程回收能力。"""
from http.server import ThreadingHTTPServer

from serve.http.handler import Handler
from serve.tasks.queue import JobManager
from serve.tasks.worker import execute
from agent.processes import enable_subreaper, sys_linux


class SubmissionServer(ThreadingHTTPServer):
    """HTTP 请求线程仅提交/查询；求解在独立队列线程中运行。"""
    daemon_threads = True
    # Accept nonce-retry/polling bursts without the default five-entry TCP backlog.
    request_queue_size = 32

    def __init__(self, address, handler=Handler, runner=None):
        """建立监听端口与队列；测试可注入 runner 替换耗时求解器。"""
        super().__init__(address,handler)
        self.subreaper = enable_subreaper()
        if sys_linux() and not self.subreaper:
            super().server_close()
            raise RuntimeError('Linux child subreaper could not be enabled')
        self.jobs = JobManager(runner or execute)

    def server_close(self):
        """关闭监听并通知后台任务回收本服务持有的进程。"""
        super().server_close()
        self.jobs.close()
