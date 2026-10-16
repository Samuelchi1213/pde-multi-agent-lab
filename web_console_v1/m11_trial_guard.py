"""M11 first real multi-agent trial: shared, fail-closed local call gate.

This is NOT a provider-enforced 10 CNY cap. Peak-price estimates with a
large safety factor are a risk control, not a bill guarantee. Default OFF.
"""
import hashlib
import json
import os
import urllib.request
from decimal import Decimal, ROUND_UP
from pathlib import Path

TRIAL_ENV = "PDE_M11_TRIAL_ENABLED"
MODEL = "deepseek-flash"
MAX_CALLS = 5
MAX_OUTPUT_TOKENS = 4096
BUDGET_CNY = Decimal("10")
PRICE_IN_CNY_PER_M = Decimal("2")
PRICE_OUT_CNY_PER_M = Decimal("8")
SAFETY_FACTOR = Decimal("4")
ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "orchestrator_v1" / "runtime" / "m11_budget_ledger.json"


class TrialBlocked(RuntimeError):
    pass


def enabled():
    return os.getenv(TRIAL_ENV) == "1"


def _round(value):
    return Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_UP)


def _read(path):
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("model") != MODEL or state.get("budget_cny") != str(BUDGET_CNY):
            raise TrialBlocked("M11 预算配置和历史账本不符，禁止继续")
        return state
    return {"trial_id": "M11-FIRST-REAL-001", "model": MODEL,
            "budget_cny": str(BUDGET_CNY), "requests": {}, "frozen": False}


def _atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("x", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)


def _lock(path):
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TrialBlocked("M11 账本有未结束的锁；人工核对后处理，禁止自动重试") from exc
    return handle, lock


def _unlock(guard):
    handle, lock = guard
    os.close(handle)
    lock.unlink()


def status():
    try:
        s = _read(LEDGER)
        requests = s["requests"]
        return {"enabled": enabled(), "frozen": s["frozen"],
                "codex": (s.get("codex") or {}).get("state", "not_started"),
                "model": MODEL, "budget_cny": str(BUDGET_CNY),
                "reserved_cny": str(sum(
                    (Decimal(v["reserved_cny"]) for v in requests.values()), Decimal(0))),
                "calls": len(requests), "max_calls": MAX_CALLS,
                "output_tokens_per_call": MAX_OUTPUT_TOKENS,
                "note": "价格为峰值估算，不保证供应商端严格限费"}
    except Exception:
        return {"enabled": enabled(), "frozen": True,
                "error": "M11 预算账本异常，真实调用一律禁止"}


def _estimate(body):
    # Conservative heuristic, NOT a tokenization guarantee or price contract.
    input_estimate = len(body)
    if input_estimate > 100000:
        raise TrialBlocked("M11 请求内容过大")
    raw = ((Decimal(input_estimate) * PRICE_IN_CNY_PER_M)
           + (Decimal(MAX_OUTPUT_TOKENS) * PRICE_OUT_CNY_PER_M))
    return _round((raw / Decimal(1000000)) * SAFETY_FACTOR)


def _validate(payload, step):
    if not enabled():
        raise TrialBlocked("真实模型防火墙关闭：本轮尚未获执行授权")
    if not isinstance(step, str) or not step.startswith(("coordinator.", "team.")):
        raise TrialBlocked("当前步骤未允许计入 M11 账本")
    if payload.get("model") != MODEL or payload.get("max_tokens") != MAX_OUTPUT_TOKENS:
        raise TrialBlocked("M11 只允许已核验模型与固定输出 token 上限")
    if payload.get("thinking") != {"type": "disabled"} or payload.get("stream", False):
        raise TrialBlocked("M11 必须为非思考、非流式请求")


def request(api_key, payload, step, *, timeout=150, transport=None, _test_path=None):
    """A request is reserved before network. Ambiguous outcome = frozen forever
    until explicit manual reconciliation; no implicit retry, including from UI.
    """
    _validate(payload, step)
    if _test_path is not None and os.getenv("PDE_M11_TEST_ONLY") != "1":
        raise TrialBlocked("仅离线测试允许自定义账本路径")
    path = Path(_test_path) if _test_path is not None else LEDGER
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    estimated = _estimate(raw)
    digest = hashlib.sha256(raw).hexdigest()
    guard = _lock(path)
    try:
        state = _read(path)
        if state["frozen"]:
            raise TrialBlocked("上次请求结果不确定，M11 已冻结")
        if any(v["state"] == "reserved" for v in state["requests"].values()):
            raise TrialBlocked("存在未完成的 DeepSeek 请求，禁止并发或在断线后重复调用")
        if step in state["requests"]:
            raise TrialBlocked("步骤已存在计费预约，禁止重复调用，即使上次已成功")
        if len(state["requests"]) >= MAX_CALLS:
            raise TrialBlocked("M11 模型请求次数已达上限")
        total = sum((Decimal(x["reserved_cny"]) for x in state["requests"].values()), Decimal(0))
        if total + estimated > BUDGET_CNY:
            raise TrialBlocked("保守预算估算超过本轮 10 元目标，拒绝发起新请求")
        state["requests"][step] = {"model": MODEL, "digest": digest,
                                   "reserved_cny": str(estimated), "state": "reserved"}
        _atomic(path, state)
    finally:
        _unlock(guard)

    # Reservation has been durably written. Every failure beyond this point,
    # even a network timeout, can mean the supplier has billed the request.
    try:
        if transport is None:
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + api_key}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        else:
            result = transport(payload)
        if not isinstance(result, dict) or not result.get("choices"):
            raise TrialBlocked("供应商回执结构无效")
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise TrialBlocked("模型回执缺少真实用量")
        if not isinstance(result["choices"][0].get("message", {}).get("content"), str):
            raise TrialBlocked("模型回执内容无效")
        guard = _lock(path)
        try:
            state = _read(path)
            record = state["requests"][step]
            if record["state"] != "reserved" or record["digest"] != digest:
                raise TrialBlocked("调用回执和预约不一致")
            record["state"] = "finished"
            record["usage"] = usage
            record["response"] = result
            _atomic(path, state)
        finally:
            _unlock(guard)
        return result
    except BaseException as exc:
        # Never retry. If storage is not writable, the durable reservation
        # remains and blocks reusing this step.
        guard = _lock(path)
        try:
            state = _read(path)
            state["frozen"] = True
            rec = state["requests"][step]
            rec["state"] = "uncertain"
            rec["error_type"] = type(exc).__name__
            _atomic(path, state)
        finally:
            _unlock(guard)
        raise


def reserve_codex(task_id, step_id):
    """One Codex execution across the entire M11 trial, not once per draft."""
    if not enabled():
        raise TrialBlocked("M11 未启用，禁止执行 Codex")
    if step_id != "developer.first" or not str(task_id).startswith("DRAFT-"):
        raise TrialBlocked("非首轮或历史任务不得执行 M11 Codex")
    guard = _lock(LEDGER)
    try:
        state = _read(LEDGER)
        if state["frozen"] or state.get("codex"):
            raise TrialBlocked("M11 已执行/预约过 Codex 或试运行冻结，禁止再次开发")
        if any(v["state"] == "reserved" for v in state["requests"].values()):
            raise TrialBlocked("DeepSeek 尚有未完成请求，不能启动 Codex")
        state["codex"] = {"task_id": task_id, "step": step_id, "state": "reserved"}
        _atomic(LEDGER, state)
    finally:
        _unlock(guard)


def finish_codex(task_id, *, success):
    """Mark success or freeze on ambiguous/failed Codex invocation."""
    guard = _lock(LEDGER)
    try:
        state = _read(LEDGER)
        record = state.get("codex")
        if not record or record["task_id"] != task_id or record["state"] != "reserved":
            raise TrialBlocked("Codex 全局回执与预约不符，必须人工核查")
        record["state"] = "finished" if success else "uncertain"
        if not success:
            state["frozen"] = True
        _atomic(LEDGER, state)
    finally:
        _unlock(guard)
