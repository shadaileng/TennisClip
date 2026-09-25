"""轨迹可视化节点：薄封装 cv_visualize（诊断 TrackNet 检出情况）。

产出叠加了球轨迹折线 / 当前帧检出标记 / 球员检测框 / HUD 统计 /
底部检出时间线的诊断视频（track_overlay.mp4，经 persists 落库），
供人工判断「网球是否被识别 / 捡球段是否混入」。
多球区分：轨迹按物理连续性切成 tracklet 分段绘制（段间断线、段配色轮换、
静止球灰标 static?、命中标记带段号），参数 break_gap / max_speed 控制断线规则。
纯诊断节点：不产生候选、不改上游值；失败按 on_failure=skip 级联降级。
"""

from __future__ import annotations

from app.utils import cv_visualize
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="post.visualize_track",
        label="轨迹可视化",
        category="post",
        description="绘制球轨迹与球员检测框叠加视频（诊断 TrackNet 检出率，多球轨迹按 tracklet 分段配色），产物 track_overlay.mp4",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="track", type=PortType.TRACK),
            Port(name="duration", type=PortType.DURATION, required=False),
        ],
        outputs=[Port(name="video", type=PortType.VIDEO)],
        params=[
            ParamSpec(key="show_players", label="绘制球员框", type="bool", default=True,
                      description="YOLO 人体检测框叠加（关闭可省大量推理时间）"),
            ParamSpec(key="trail_seconds", label="轨迹回看", type="float", default=2.0,
                      min=0.2, max=10.0, description="当前帧往前多少秒的轨迹连成折线（秒）"),
            ParamSpec(key="ball_conf", label="球置信度达标线", type="float", default=0.5,
                      min=0.0, max=1.0, description="当前帧检出点低于此值标橙，达标标绿"),
            ParamSpec(key="break_gap", label="断线间隔", type="float", default=0.5,
                      min=0.1, max=3.0,
                      description="多球分段：相邻点间隔超过此秒数断线（漏检久不连线）"),
            ParamSpec(key="max_speed", label="跳变速度上限", type="float", default=10000.0,
                      min=1000.0, max=60000.0,
                      description="多球分段：隐含速度超过此 px/s 判为换球断线；镜头特写（球场占画面比例大）时调大"),
            ParamSpec(key="player_stride", label="球员检测步长", type="int", default=3,
                      min=1, max=30, description="每 N 帧跑一次球员检测，中间帧复用最近框"),
            ParamSpec(key="player_model", label="球员模型", type="select", default="n",
                      options=["n", "s", "m"],
                      description="YOLOv8 规格：n=快 / m=准"),
            ParamSpec(key="device", label="推理设备", type="select", default="auto",
                      options=["auto", "cuda", "cpu"],
                      description="auto=有 GPU 用 GPU 否则 CPU；cuda=强制 GPU；cpu=强制 CPU"),
        ],
        stage="postprocessing",
        on_failure="skip",  # 诊断节点，CV 依赖/权重缺失不拖垮任务
        persists=(("video", "track_overlay"),),
    )
)
def run(ctx, params):
    """渲染轨迹叠加诊断视频，输出 video 端口并经 persists 落库。"""
    video = ctx.inputs.get("video")
    track = ctx.inputs.get("track")

    if video is None:
        raise ValueError("节点 post.visualize_track 的必填输入 video 未连接")
    if track is None:
        raise ValueError("节点 post.visualize_track 的必填输入 track 未连接")

    # duration 可选：缺省时渲染器回退为帧数/帧率推算时间线总长
    duration = ctx.inputs.get("duration") or 0.0
    out_path = ctx.task_out / "track_overlay.mp4"

    rendered = cv_visualize.render_track_overlay(
        video, track, out_path,
        duration=float(duration),
        show_players=params["show_players"],
        trail_seconds=params["trail_seconds"],
        ball_conf=params["ball_conf"],
        break_gap=params["break_gap"],
        max_speed=params["max_speed"],
        player_stride=params["player_stride"],
        model_size=params["player_model"],
        device=params.get("device", "auto"),
        task_id=getattr(ctx, "task_id", ""),
    )
    return {"video": rendered}
