"""核心数据模型：高光时间戳、分析报告、任务结果。"""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """单个高光片段的时间戳。"""
    start: float = Field(description="片段起始时间（秒）")
    end: float = Field(description="片段结束时间（秒）")
    label: str = Field(description="片段类型，如 ace / rally / winner / smash")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="识别置信度 0-1")


class HighlightResult(BaseModel):
    """高光识别结构化结果。"""
    segments: List[Segment] = Field(default_factory=list)
    target_duration: int = Field(default=15, description="集锦目标时长（秒）")
    scene_type: str = Field(default="unknown", description="场景类型：training / match / practice")
    reasoning: str = ""


class StrokeAnalysis(BaseModel):
    """单个技术动作的分析。"""
    action: str = Field(description="动作类型：forehand / backhand / serve / volley / footwork / grip / posture")
    problem: str = ""
    cause: str = ""
    suggestion: str = ""
    severity: str = Field(default="moderate", description="low / moderate / high")


class TechnicalReport(BaseModel):
    """结构化动作技术分析报告。"""
    level: str = Field(default="intermediate", description="分析层级：beginner / intermediate / professional")
    summary: str = ""
    strokes: List[StrokeAnalysis] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    training_plan: List[str] = Field(default_factory=list)
    generated_by: str = "TennisClip AI"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskResult(BaseModel):
    """一条视频处理任务的完整结果。"""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    stage: str = "pending"  # 当前处理阶段：pending/preprocessing/highlighting/editing/reporting（终态沿用 status）
    source_video: str = ""
    highlight: Optional[HighlightResult] = None
    report: Optional[TechnicalReport] = None
    highlight_video_path: Optional[str] = None
    report_path: Optional[str] = None
    report_error: Optional[str] = None  # 报告生成失败原因（非致命，保留已生成的高光）
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
