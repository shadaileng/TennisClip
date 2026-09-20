"""LLM 客户端（OpenAI Chat Completions 兼容协议）。

- 使用 OpenAI 官方 SDK 的 OpenAI() 客户端格式，按 provider 三要素构造：
  - base_url  : OpenAI 兼容端点（含 /v1）
  - api_key   : 由 model_providers 表直接存储（api_key 字段），DB 分支优先；静态兜底回退 env
  - model     : 请求体 "model" 字段
  支持任何 OpenAI 兼容端点：OpenAI 官方、StepFun、Ollama、vLLM 等。
- mock_mode:
  - auto   : 无 API Key 时自动回退 Mock
  - always : 强制 Mock（离线开发/测试）
  - never  : 必须真实调用，否则抛错
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Tuple

try:
    from openai import OpenAI
except ImportError:  # 允许仅 Mock 模式运行
    OpenAI = None

from app.config import AppConfig
from app.models import HighlightResult, TechnicalReport
from app.utils.logger import get_logger
from app.utils.media_strategies import get_strategy

logger = get_logger(__name__)

# 日志中单条输入/输出内容的最大保留字符数（超出截断，避免 base64 帧或长文本撑爆日志）
_MAX_LOG_CHARS = 2000


def _trunc(value) -> str:
    """将任意值转字符串并截断到 _MAX_LOG_CHARS，用于 DEBUG 级日志安全输出。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if text is None:
        return ""
    if len(text) > _MAX_LOG_CHARS:
        return text[:_MAX_LOG_CHARS] + f"...(截断, 共{len(text)}字)"
    return text


def _is_mock_mode(config: AppConfig, api_key: Optional[str] = None) -> bool:
    if api_key is None:
        api_key = config.api_key
    mode = (config.llm.mock_mode or "auto").lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return not api_key


def _resolve_provider(config: AppConfig) -> Tuple[object, str]:
    """解析当前生效提供商（优先配置 KV 引用的服务商，回退静态配置）。

    返回 (provider, api_key)：provider 具 name / base_url / model 属性。
    使 /api/v1/config 切换 ai.provider / ai.model 在推理时真正生效。
    """
    from app.services import config_service, db_service

    try:
        with db_service.session() as s:
            ai = config_service.get_ai_config(s, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm: 提供商解析回退静态配置（DB 查询失败）：{}", exc)
        try:
            active = config.active_provider
            base = active.base_url
            model = active.model or (active.models[0] if active.models else "")
        except ValueError:
            base, model = "", ""
        ai = config_service.AIConfig(
            api_key=config.api_key, base_url=base, model=model,
            provider="custom", source="config",
        )

    api_key = ai.api_key or ""
    provider = SimpleNamespace(name=ai.provider, base_url=ai.base_url, model=ai.model)
    logger.info(
        "llm: 生效服务商={} 模型={} base_url={} 来源={} api_key_set={} mock_mode={}",
        ai.provider, ai.model, ai.base_url, ai.source,
        bool(ai.api_key), (config.llm.mock_mode or "auto").lower(),
    )
    return provider, api_key


def resolve_effective(config: AppConfig) -> Tuple[str, bool]:
    """一次性解析：返回 (生效模型名, 是否 Mock 模式)。

    仅调用一次 _resolve_provider，避免 get_effective_model / is_mock_mode 各自解析
    导致的冗余 DB 查询（report 阶段尤为明显：原需 3 次，合并后 2 次）。
    解析失败保守回落 ('', True)。
    """
    try:
        provider, api_key = _resolve_provider(config)
        return (provider.model or "", _is_mock_mode(config, api_key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm: 解析生效模型/模式失败，回落 ('', True)：{}", exc)
        return ("", True)


def get_effective_model(config: AppConfig) -> str:
    """返回当前生效模型名（供报告 generated_by 标注真实模型）。

    复用 resolve_effective 的解析结果（ai.model 覆盖 > 服务商 default_model > 静态 fallback）。
    解析失败（如 DB 不可用）回落空串，由调用方决定兜底文案。
    """
    return resolve_effective(config)[0]


def is_mock_mode(config: AppConfig) -> bool:
    """当前是否处于 Mock 模式（解析真实 api_key 后判定），供报告 mock 标注使用。

    注意：必须解析生效提供商的真实 api_key 再判定；直接以 config.api_key 或非 api_key
    形参调用 _is_mock_mode 会漏掉 DB 中配置的服务商 key，误判为 mock。
    auto 模式下以“生效提供商是否配了 API Key”为准；解析失败保守回落 mock。
    """
    return resolve_effective(config)[1]


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON（容忍 markdown 代码块包裹）。"""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("JSON 解析失败，原文前 200 字: {}", text[:200])
        return None


def _collect_fallback_text(message) -> str:
    """聚合 content 之外的可能承载答案的字段（推理模型的 reasoning_content / model_extra）。

    部分推理模型（如 step-3.7-flash）会把最终答案放在 reasoning_content 或厂商私有字段，
    而 message.content 为空；此处兜底收集所有字符串形式候选文本供 JSON 解析。
    """
    parts: list[str] = []
    rc = getattr(message, "reasoning_content", None)
    if isinstance(rc, str) and rc.strip():
        parts.append(rc)
    # StepFun 等推理模型把思考放在 message.reasoning（与 reasoning_content 不同字段）
    r = getattr(message, "reasoning", None)
    if isinstance(r, str) and r.strip():
        parts.append(r)
    extra = getattr(message, "model_extra", None) or {}
    for v in extra.values():
        if isinstance(v, str) and v.strip():
            parts.append(v)
        elif isinstance(v, dict):
            for sv in v.values():
                if isinstance(sv, str) and sv.strip():
                    parts.append(sv)
    return "\n".join(parts)


def _dump_response(response) -> str:
    """把完整响应对象序列化为字符串（截断），便于定位空 content 等异常。"""
    try:
        return json.dumps(response.model_dump(), ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(response)


def complete_structured(
    video_path: Path,
    prompt: str,
    config: AppConfig,
    schema_hint: str = "HighlightResult",
    candidates=None,
    analysis_mode: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Optional[dict]:
    """按 OpenAI Chat Completions 格式发送 Prompt（含视频媒体引用），返回解析后的结构化 dict。

    candidates: 信号定位的候选窗口（Segment 列表），用于抽帧段内定位与视频模式软提示。
    analysis_mode: 媒体输入策略（frame=抽帧 / video=视频理解）；空则取 config.llm.analysis_mode。
    """
    provider, api_key = _resolve_provider(config)
    if model_override:
        provider.model = model_override
    if _is_mock_mode(config, api_key):
        logger.info(
            "llm: 进入 mock 模式（schema={}），未实际调用模型 provider={} model={}",
            schema_hint, provider.name, provider.model,
        )
        result = _mock_structured(schema_hint, config, video_path)
        logger.debug("llm: mock 输出样例(前{}字)={}", _MAX_LOG_CHARS, _trunc(result))
        return result

    if OpenAI is None:
        raise RuntimeError(
            "openai SDK 未安装，无法调用真实 API；"
            "uv sync 安装依赖或改用 mock 模式"
        )

    logger.info(
        "llm: 真实调用 provider={} model={} base_url={} api_key_set={}",
        provider.name, provider.model, provider.base_url, bool(api_key),
    )

    # OpenAI 官方客户端格式（按 provider 三要素构造）
    client = OpenAI(
        base_url=provider.base_url,
        api_key=api_key,
        timeout=config.llm.timeout_seconds,
        max_retries=config.llm.max_retries,
    )

    # 媒体输入策略分发（frame 抽帧 / video 视频理解）
    strategy = get_strategy(analysis_mode or config.llm.analysis_mode)
    media_parts = strategy.build_media_parts(video_path, candidates, config)
    if strategy.name == "video":
        # 视频理解：媒体块排在文本之前（StepFun 最佳实践）
        user_content = [*media_parts, {"type": "text", "text": prompt}]
    else:
        user_content = [{"type": "text", "text": prompt}, *media_parts]

    logger.debug(
        "llm: 输入 prompt(前{}字)={} 策略={} 媒体块数={} 模型={}",
        _MAX_LOG_CHARS, _trunc(prompt), strategy.name, len(media_parts), provider.model,
    )

    messages = [
        {
            "role": "system",
            "content": "你是网球视频分析 Agent，按用户要求输出 JSON，不输出多余文字。",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]
    # 结构化抽取无需长链推理；StepFun 的 Chat Completions 端点用 reasoning_effort 控制推理强度
    # （"low" 即为最小推理，enable_thinking 仅百炼托管版生效，这里两者都传以兼容不同部署）。
    # 关闭/压低推理可避免推理 token 耗尽导致 length 截断，也更省时省钱。
    reasoning_effort = "low" if not config.llm.enable_thinking else "medium"
    extra_body = {
        "enable_thinking": config.llm.enable_thinking,
        "reasoning_effort": reasoning_effort,
    }
    max_tokens = config.llm.max_tokens
    response = None
    parsed = None
    video_retries = 2  # StepFun 视频解码瞬时失败（video_exception/conn closed）的重试次数
    for v_attempt in range(video_retries + 1):
        try:
            response = client.chat.completions.create(
                model=provider.model,
                messages=messages,
                temperature=config.llm.temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            is_video_infra = (
                "video_exception" in msg or "conn is closed" in msg or "ReadFrame" in msg
            )
            if is_video_infra and v_attempt < video_retries:
                logger.warning(
                    "llm: StepFun 视频解码瞬时失败（{}/{}），2s 后重试：{}",
                    v_attempt + 1, video_retries, msg[:200],
                )
                time.sleep(2)
                continue
            logger.error(
                "llm: 调用失败 provider={} model={} base_url={}：{}",
                provider.name, provider.model, provider.base_url, exc,
            )
            raise
        message = response.choices[0].message
        content = message.content
        logger.debug("llm: 输出 content(前{}字)={} 模型={}", _MAX_LOG_CHARS, _trunc(content), provider.model)
        parsed = _extract_json(content)
        if parsed is None:
            # 推理模型偶把答案置于 reasoning / reasoning_content / model_extra 而 content 为空，兜底解析
            fallback = _collect_fallback_text(message)
            if fallback:
                parsed = _extract_json(fallback)
                if parsed is not None:
                    logger.warning(
                        "llm: content 为空，已从 reasoning/model_extra 兜底解析成功 模型={}",
                        provider.model,
                    )
        if parsed is not None:
            break
        reason = getattr(response.choices[0], "finish_reason", None)
        if reason == "length" and max_tokens < 32768:
            # 推理预算耗尽被截断（reasoning 模型 max_tokens 不足）：提高上限重试
            max_tokens = min(max_tokens * 2, 32768)
            logger.warning(
                "llm: finish_reason=length，提高 max_tokens 至 {} 重试 模型={}",
                max_tokens, provider.model,
            )
            continue
        # 仍无可解析 JSON：转储原始响应，定位 StepFun 空 content（内容审核/超长/异常）根因
        logger.warning(
            "llm: 返回 content 为空或无法解析（finish_reason={}），原始响应(前{}字)={} 模型={}",
            reason, _MAX_LOG_CHARS, _trunc(_dump_response(response)), provider.model,
        )
        break
    logger.debug("llm: 解析结果(前{}字)={} 模型={}", _MAX_LOG_CHARS, _trunc(parsed), provider.model)
    return parsed


def _mock_structured(schema_hint: str, config: AppConfig, video_path: Path) -> Optional[dict]:
    """无 API 时的示例结构化返回，保证全链路可联调。"""
    if schema_hint == "HighlightResult":
        return HighlightResult(
            segments=[
                {"start": 3.0, "end": 7.5, "label": "ace", "confidence": 0.95},
                {"start": 20.0, "end": 24.5, "label": "rally", "confidence": 0.9},
                {"start": 41.0, "end": 45.0, "label": "winner", "confidence": 0.88},
            ],
            target_duration=config.highlight.target_duration,
            scene_type="training",
            reasoning="mock: 示例高光回合（无 API 回退）",
        ).model_dump()
    if schema_hint == "TechnicalReport":
        return TechnicalReport(
            level="intermediate",
            summary="mock: 示例分析报告，接入真实 API 后替换。",
            strokes=[
                {"action": "forehand", "problem": "击球点偏后", "cause": "转髋不足", "suggestion": "前击多球训练", "severity": "moderate"}
            ],
            strengths=["多拍稳定性"],
            weaknesses=["二发成功率偏低"],
            training_plan=["每周 2 次正手前击多球"],
        ).model_dump()
    return None
