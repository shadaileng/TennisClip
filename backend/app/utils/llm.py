"""LLM 客户端（OpenAI Chat Completions 兼容协议）。

- 使用 OpenAI 官方 SDK 的 OpenAI() 客户端格式，按 provider 三要素构造：
  - base_url  : OpenAI 兼容端点（含 /v1）
  - api_key   : 由 .env 中当前 provider 的 api_key_env 指定变量读取
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
import re
from pathlib import Path
from typing import Optional

try:
    from openai import OpenAI
except ImportError:  # 允许仅 Mock 模式运行
    OpenAI = None

from app.config import AppConfig
from app.models import HighlightResult, TechnicalReport
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _is_mock_mode(config: AppConfig) -> bool:
    mode = (config.llm.mock_mode or "auto").lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return not config.api_key


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
        logger.warning("JSON 解析失败，原文前 200 字: %s", text[:200])
        return None


def _video_to_image_frames(video_path: Path, max_frames: int = 12) -> list[str]:
    """将本地视频路径转为 base64 图像帧（OpenAI 多模态 image_url 格式）。

    OpenAI Chat Completions 支持 image_url（base64 data URI），
    但不支持视频直传。此函数用 FFMPEG 抽帧后逐帧注入。
    无 FFMPEG 时返回空列表（纯文本模式）。
    """
    from app.utils import ffmpeg
    if not ffmpeg.is_available():
        return []
    try:
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            probe = ffmpeg.probe(video_path)
            duration = probe.get("duration", 30)
            step = max(duration / max_frames, 0.5)
            frames = []
            for i in range(max_frames):
                ts = i * step
                if ts >= duration:
                    break
                out = Path(tmp) / f"frame_{i:03d}.jpg"
                ffmpeg.run([
                    "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(out),
                ])
                b64 = base64.b64encode(out.read_bytes()).decode()
                frames.append(f"data:image/jpeg;base64,{b64}")
            return frames
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频抽帧失败，降级为纯文本: %s", exc)
        return []


def complete_structured(
    video_path: Path,
    prompt: str,
    config: AppConfig,
    schema_hint: str = "HighlightResult",
) -> Optional[dict]:
    """按 OpenAI Chat Completions 格式发送 Prompt（含视频帧引用），返回解析后的结构化 dict。"""
    if _is_mock_mode(config):
        logger.info("llm: mock mode (schema=%s)", schema_hint)
        return _mock_structured(schema_hint, config, video_path)

    if OpenAI is None:
        raise RuntimeError(
            "openai SDK 未安装，无法调用真实 API；"
            "uv sync 安装依赖或改用 mock 模式"
        )

    provider = config.active_provider
    logger.info("llm: provider=%s model=%s", provider.name, provider.model)

    # OpenAI 官方客户端格式（按 provider 三要素构造）
    client = OpenAI(
        base_url=provider.base_url,
        api_key=config.api_key,
        timeout=config.llm.timeout_seconds,
    )

    # 构建多模态消息（OpenAI 格式：image_url 支持 base64 data URI）
    frames = _video_to_image_frames(video_path)
    user_content = [{"type": "text", "text": prompt}]
    for frame in frames:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": frame},
        })

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
    content = response.choices[0].message.content
    return _extract_json(content)


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
