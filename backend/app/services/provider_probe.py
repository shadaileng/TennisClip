"""模型可用性校验（check-models）：参考 TennisDiary ai_providers 路由。

优先 GET {base_url}/models 解析可用模型清单（list 策略）；
不支持时回落逐模型 POST chat/completions（max_tokens=1）探测（probe 策略）。
连接/超时级失败返回 ok=False（镜像 ai-connect 语义），不阻塞主流程。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.utils.logger import get_logger

log = get_logger("config")

_CHECK_TIMEOUT_SECONDS = 15


def _extract_available_models(data: dict) -> list[str]:
    """从 OpenAI 兼容 /models 响应解析可用模型 ID（data[]/models[]，取 id/name/字符串）。"""
    payload = data.get("data") if isinstance(data.get("data"), list) else None
    if payload is None:
        payload = data.get("models") if isinstance(data.get("models"), list) else None
    available: list[str] = []
    for item in payload or []:
        if isinstance(item, str):
            available.append(item)
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name")
            if name:
                available.append(str(name))
    return available


async def _probe_model(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """对单个模型发最小文本探测（max_tokens=1，不耗图片 token）。

    模型名不存在时上游秒回非 200（如 503 model_not_found），探测即识别。
    """
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code == 200:
        return True, "可用"
    text = (resp.text or "")[:120].replace("\n", " ")
    return False, f"不可用（HTTP {resp.status_code}）：{text}"


async def check_models(base_url: str, api_key: str, models: list[str]) -> dict[str, Any]:
    """校验模型可用性：list 优先，不支持则逐模型 probe。

    返回 { ok, strategy, available?, results: [{model, ok, message}], message? }。
    """
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return {"ok": False, "strategy": "list", "results": [], "message": "base_url 为空"}
    url = f"{base_url}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "message": "连接超时（15 秒）", "url": url, "results": []}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"网络异常: {e!s}", "url": url, "results": []}

    if resp.status_code == 200:
        try:
            available = _extract_available_models(resp.json())
        except ValueError as exc:
            log.debug("模型列表 JSON 解析失败: {}", exc)
            available = []
        if available:
            results = [
                {
                    "model": m,
                    "ok": m in available,
                    "message": "可用" if m in available else "不在服务商模型列表中",
                }
                for m in models
            ]
            return {"ok": True, "strategy": "list", "available": available, "results": results}
        # 200 但无法解析模型列表 → 回落 probe
    elif resp.status_code in (401, 403):
        return {
            "ok": False,
            "status_code": resp.status_code,
            "message": f"鉴权失败（HTTP {resp.status_code}），请检查 API Key",
            "results": [],
        }

    # probe 兜底：逐模型最小文本探测
    try:
        results = []
        for model in dict.fromkeys(models):
            ok, message = await _probe_model(base_url, api_key, model)
            results.append({"model": model, "ok": ok, "message": message})
    except httpx.TimeoutException:
        return {"ok": False, "message": "连接超时（15 秒）", "url": base_url, "results": []}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"网络异常: {e!s}", "url": base_url, "results": []}

    return {"ok": True, "strategy": "probe", "results": results}
