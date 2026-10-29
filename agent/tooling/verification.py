"""Per-task verification data; behavior lives in registered tools."""


class VerificationState:
    """维护同一题的验证状态，保证测试修订与反例证据一致。"""

    def __init__(self, record):
        """保留受保护的记录入口以及跨工具共享的验证状态。"""
        self.record = record
        self.last_simulation = None
        self.pending_counterexample = None
        self.report_sources = {}
        self.report_reads = {}
