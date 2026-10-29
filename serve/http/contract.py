"""投题契约校验；只校验公开字段，不启动任务或访问模型。"""
import math


def validate_payload(payload):
    """拒绝字段类型、模式或期限不符合约定的请求。"""
    if not isinstance(payload, dict):
        raise ValueError()
    for key in ("task_id", "nonce", "prompt", "interface", "mode"):
        if not isinstance(payload.get(key), str):
            raise ValueError()
    duration = payload.get("deadline_s")
    if payload["mode"] not in ("agent", "baseline"):
        raise ValueError()
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 0 < duration <= 3600):
        raise ValueError()
