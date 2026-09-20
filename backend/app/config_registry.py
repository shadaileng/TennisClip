"""配置项注册表（最小集，参考 TennisDiary config_registry）。

仅注册「服务商管理」相关的四项覆盖项，默认值取自 AppConfig：
- ai.provider : 直选生效的模型服务商名（select，选项动态取启用服务商 + custom）
- ai.model    : 覆盖所选服务商的默认模型（str，空=跟随默认）
- ai.api_key  : 独立配置（custom）时的 API Key（secret）
- ai.base_url : 独立配置（custom）时的 OpenAI 兼容基地址（url）

激活服务商与选定模型从 provider 行内列（is_active/selected_model）抽离到这里的
system_config KV；provider 表退化为纯凭据目录（ai_providers）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CATEGORY_AI = "ai"
CATEGORY_PIPELINE = "pipeline"

# 管线阶段固定顺序与合法取值（用于 pipeline.stages 校验与解析）
DEFAULT_STAGES: list[str] = ["preprocess", "highlight", "edit", "report"]
VALID_STAGES: set[str] = set(DEFAULT_STAGES)
DEFAULT_STAGES_JSON = '["preprocess","highlight","edit","report"]'

VALUE_TYPE_STR = "str"
VALUE_TYPE_SECRET = "secret"
VALUE_TYPE_URL = "url"
VALUE_TYPE_SELECT = "select"
VALUE_TYPE_INT = "int"
VALUE_TYPE_BOOL = "bool"

SOURCE_DB = "db"
SOURCE_ENV = "env"
SOURCE_BUILTIN = "builtin"


@dataclass(frozen=True)
class ConfigItem:
    """单个配置项定义（不含运行时覆盖值）。"""

    key: str
    category: str
    label: str
    description: str
    value_type: str
    editable: bool
    default: str
    env_key: Optional[str] = None
    options: Optional[list[str]] = None


def build_config_items(config) -> list[ConfigItem]:
    """依据 AppConfig 构建配置项注册表（默认值取自 config）。"""
    active = config.llm.active_provider if config.llm.providers else "custom"
    return [
        ConfigItem(
            key="ai.provider",
            category=CATEGORY_AI,
            label="当前模型服务商",
            description="直选生效的模型服务商名称；custom 表示使用下方独立配置",
            value_type=VALUE_TYPE_SELECT,
            editable=True,
            default=active,
            env_key=None,
            options=None,  # 动态取启用服务商 + custom（见 config_service._item_options）
        ),
        ConfigItem(
            key="ai.model",
            category=CATEGORY_AI,
            label="模型",
            description="覆盖所选服务商的默认模型；空表示跟随服务商默认",
            value_type=VALUE_TYPE_STR,
            editable=True,
            default="",
            env_key=None,
        ),
        ConfigItem(
            key="ai.api_key",
            category=CATEGORY_AI,
            label="API Key",
            description="独立配置（custom）时的 API Key；引用服务商时由服务商条目提供",
            value_type=VALUE_TYPE_SECRET,
            editable=True,
            default="",
            env_key=None,
        ),
        ConfigItem(
            key="ai.base_url",
            category=CATEGORY_AI,
            label="Base URL",
            description="独立配置（custom）时的 OpenAI 兼容基地址",
            value_type=VALUE_TYPE_URL,
            editable=True,
            default="",
            env_key=None,
        ),
        ConfigItem(
            key="llm.analysis_level",
            category=CATEGORY_PIPELINE,
            label="分析层级",
            description="高光识别与报告的分析深度；all=所有高光回合（遍历整段、截取全部、不截断时长）",
            value_type=VALUE_TYPE_SELECT,
            editable=True,
            default="intermediate",
            env_key=None,
            options=["beginner", "intermediate", "professional", "all"],
        ),
        ConfigItem(
            key="llm.highlight_strategy",
            category=CATEGORY_PIPELINE,
            label="高光识别媒体策略",
            description="高光识别媒体输入策略：frame=抽帧（默认）；video=视频理解（整段视频直送）",
            value_type=VALUE_TYPE_SELECT,
            editable=True,
            default="frame",
            env_key=None,
            options=["frame", "video"],
        ),
        ConfigItem(
            key="pipeline.stages",
            category=CATEGORY_PIPELINE,
            label="管线阶段",
            description="启用的管线阶段有序列表（JSON 数组），固定顺序：预处理→高光识别→剪辑→报告",
            value_type=VALUE_TYPE_STR,
            editable=True,
            default=DEFAULT_STAGES_JSON,
            env_key=None,
        ),
        ConfigItem(
            key="workflow.mode",
            category=CATEGORY_PIPELINE,
            label="工作流执行模式",
            description="legacy=旧固定管线（默认）；workflow=可编排工作流",
            value_type=VALUE_TYPE_SELECT,
            editable=True,
            default="legacy",
            env_key=None,
            options=["legacy", "workflow"],
        ),
        ConfigItem(
            key="workflow.default_graph_id",
            category=CATEGORY_PIPELINE,
            label="默认工作流",
            description="默认执行的预置工作流 ID（含空字符串=内置默认图）",
            value_type=VALUE_TYPE_STR,
            editable=True,
            default="",
            env_key=None,
        ),
    ]


def find_config_item(key: str, config) -> Optional[ConfigItem]:
    """按 key 查找注册项；不存在返回 None。"""
    for item in build_config_items(config):
        if item.key == key:
            return item
    return None
