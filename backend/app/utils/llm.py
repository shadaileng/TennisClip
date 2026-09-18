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

import base64
import json
import os
import re
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


def _video_to_image_frames(video_path: Path, candidates=None, max_frames: int = 30) -> list[tuple[float, str]]:
    """将本地视频路径转为 (时间戳, base64 图像帧) 列表（OpenAI 多模态 image_url 格式）。

    OpenAI Chat Completions 支持 image_url（base64 data URI），但不支持视频直传。
    此函数用 FFMPEG 抽帧，并为每帧记录其对应时间戳，使模型能把视觉内容映射回真实秒数。

    - 给定 candidates（信号候选窗口）时：在每段窗口内均匀抽帧，让模型看到真实动作；
    - 否则：全片均匀抽帧（回退方案）。
    无 FFMPEG 时返回空列表（纯文本模式）。
    """
    from app.utils import ffmpeg
    if not ffmpeg.is_available():
        return []
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            probe = ffmpeg.probe(video_path)
            duration = probe.get("duration", 30)
            if candidates:
                # 在每个候选窗口内均匀抽帧，每段 per 帧，整体上限 max_frames
                samples = []
                per = max(1, max_frames // max(1, len(candidates)))
                for c in candidates:
                    s = max(0.0, float(getattr(c, "start", 0)))
                    e = min(float(duration), float(getattr(c, "end", duration)))
                    if e - s < 0.3:
                        continue
                    for k in range(per):
                        ts = s + (e - s) * (k + 0.5) / per
                        samples.append(ts)
            else:
                step = max(duration / max_frames, 1.0)
                samples = [i * step for i in range(max_frames) if i * step < duration]
            frames = []
            for ts in samples[:max_frames]:
                out = Path(tmp) / f"frame_{len(frames):03d}.jpg"
                ffmpeg.run([
                    "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(out),
                ])
                b64 = base64.b64encode(out.read_bytes()).decode()
                frames.append((round(ts, 2), f"data:image/jpeg;base64,{b64}"))
            return frames
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频抽帧失败，降级为纯文本: {}", exc)
        return []


def complete_structured(
    video_path: Path,
    prompt: str,
    config: AppConfig,
    schema_hint: str = "HighlightResult",
    candidates=None,
) -> Optional[dict]:
    """按 OpenAI Chat Completions 格式发送 Prompt（含视频帧引用），返回解析后的结构化 dict。

    candidates: 信号定位的候选窗口（Segment 列表），用于段内抽帧与 prompt 约束。
    """
    provider, api_key = _resolve_provider(config)
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

    # 构建多模态消息（OpenAI 格式：image_url 支持 base64 data URI）
    frames = _video_to_image_frames(
        video_path, candidates=candidates, max_frames=config.llm.max_frames,
    )
    user_content = [{"type": "text", "text": prompt}]
    if frames:
        if candidates:
            frame_intro = (
                f"以下为各候选窗口内抽帧，共 {len(frames)} 帧，已标注对应时间戳（秒）。"
                f"请据此在候选窗口 ±2s 内给出准确起止秒数。"
            )
        else:
            frame_intro = (
                f"以下为视频按时长均匀抽帧，共 {len(frames)} 帧，按时间顺序排列，"
                f"每帧前已标注其对应时间戳（秒）。请依据各帧时间戳给出准确的起止秒数，"
                f"不要凭空臆造视频中未出现的时间点。"
            )
        user_content.append({
            "type": "text",
            "text": frame_intro,
        })
        for idx, (ts, frame) in enumerate(frames):
            user_content.append({"type": "text", "text": f"[第 {idx + 1} 帧 @ {ts:.1f}s]"})
            user_content.append({"type": "image_url", "image_url": {"url": frame}})
    else:
        user_content.append({
            "type": "text",
            "text": "（未能抽帧，仅基于文字描述分析）",
        })

    logger.debug(
        "llm: 输入 prompt(前{}字)={} 帧数={} 帧时间戳={} 模型={}",
        _MAX_LOG_CHARS, _trunc(prompt), len(frames),
        [ts for ts, _ in frames], provider.model,
    )

    try:
        response = client.chat.completions.create(
            model=provider.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是网球视频分析 Agent，按用户要求输出 JSON，不输出多余文字。",
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "llm: 调用失败 provider={} model={} base_url={}：{}",
            provider.name, provider.model, provider.base_url, exc,
        )
        raise
    content = response.choices[0].message.content
    logger.debug("llm: 输出 content(前{}字)={} 模型={}", _MAX_LOG_CHARS, _trunc(content), provider.model)
    parsed = _extract_json(content)
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
