"""动态配置服务：DB 覆盖 > 默认值，请求时实时解析（参考 TennisDiary config_service）。

配置项定义见 app.core.config_registry；system_config 表只存覆盖值，无覆盖行时即默认值。

核心职责：
- mask_secret            : 敏感值掩码（前3 + 末4）
- get_config_value       : 读取生效值（DB 覆盖 > 默认值）
- set_config_value       : 写入/删除覆盖值
- delete_config_value    : 删除覆盖行（恢复默认）
- get_ai_config          : AI 三件套生效配置（服务商引用 > 独立覆盖 > 默认值）
- list_provider_options  : 启用服务商名 + custom
- build_config_list/item : 给前端 /config 端点用的结构化响应
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config_registry import (
    SOURCE_BUILTIN,
    SOURCE_DB,
    SOURCE_ENV,
    VALUE_TYPE_SECRET,
    VALUE_TYPE_URL,
    find_config_item,
)
from app.db_models import AiProvider, SystemConfig
from app.utils.logger import get_logger

log = get_logger("config")

MASK_PLACEHOLDER = "****"


def mask_secret(value: str) -> str:
    """通用敏感值掩码：{前3位}****{末4位}；过短或空值返回掩码/空串。

    与 TennisDiary 语义一致（替换原「首尾各4位」）。
    """
    if not value:
        return ""
    if len(value) <= 8:
        return MASK_PLACEHOLDER
    return f"{value[:3]}{MASK_PLACEHOLDER}{value[-4:]}"


@dataclass(frozen=True)
class AIConfig:
    """AI 网关生效配置（服务商引用 > DB 覆盖 > 默认值）。"""

    api_key: str
    base_url: str
    model: str
    provider: str = "custom"  # 当前生效服务商名；custom = 独立配置
    source: str = "config"    # database | config（生效来源）


def _load_config() -> Any:
    from app.config import load_config

    return load_config()


def _get_override_value(db: Session, key: str) -> Optional[str]:
    """仅返回显式覆盖值（无覆盖行 = None）。"""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row is not None and row.value is not None else None


def get_config_value(db: Session, key: str, config: Any = None) -> str:
    """读取生效值：DB 覆盖 > 默认值。"""
    item = find_config_item(key, config or _load_config())
    if item is None:
        return ""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row is not None and row.value is not None else item.default


def _validate_value(item, value: str) -> str:
    """按 value_type 校验并归一化；非法值抛 400。"""
    vtype = item.value_type
    if vtype == VALUE_TYPE_URL:
        if not str(value).strip().lower().startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400, detail=f"{item.label} 必须是 http(s):// 开头的合法地址"
            )
        return str(value).strip()
    return value


def set_config_value(db: Session, key: str, value: str, config: Any = None) -> dict[str, Any]:
    """设置覆盖值；secret 空值/掩码值=保持不变；等于默认值=归一化删行。"""
    item = find_config_item(key, config or _load_config())
    if item is None:
        raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
    if not item.editable:
        raise HTTPException(status_code=403, detail=f"配置项 {item.label} 不可动态编辑")

    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()

    if item.value_type == VALUE_TYPE_SECRET:
        current = row.value if row is not None else item.default
        if value == "" or value == mask_secret(current):
            return _build_item(db, key, config)

    normalized = _validate_value(item, value)

    if normalized == item.default:
        if row is not None:
            db.delete(row)
            db.commit()
    else:
        if row is None:
            row = SystemConfig(key=key, value=normalized)
            db.add(row)
        else:
            row.value = normalized
        db.commit()

    return _build_item(db, key, config)


def delete_config_value(db: Session, key: str, config: Any = None) -> dict[str, Any]:
    """删除覆盖行（恢复默认）。"""
    item = find_config_item(key, config or _load_config())
    if item is None:
        raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return _build_item(db, key, config)


def _resolve_ai_config(
    db: Session,
    provider_name: str,
    model_override: Optional[str],
    api_key_override: Optional[str],
    base_url_override: Optional[str],
    config: Any,
) -> AIConfig:
    """按 ai.provider 引用解析：选中且启用的服务商→引用其条目，否则回落独立配置。"""
    if provider_name and provider_name != "custom":
        provider = db.query(AiProvider).filter(AiProvider.name == provider_name, AiProvider.enabled == 1).first()
        if provider is not None:
            return AIConfig(
                api_key=(provider.api_key or "") or (api_key_override or ""),
                base_url=provider.base_url,
                model=model_override or provider.default_model,
                provider=provider.name,
                source="database",
            )
    # 独立配置（custom 或未命中）：覆盖 > 静态默认值
    try:
        active = config.active_provider
        fallback_base = active.base_url
        fallback_model = active.model or (active.models[0] if active.models else "")
        fallback_key = config.api_key
    except Exception as exc:  # noqa: BLE001
        log.debug("config: 静态 active_provider 解析失败，回落空值: {}", exc)
        fallback_base = ""
        fallback_model = ""
        fallback_key = ""
    return AIConfig(
        api_key=api_key_override or fallback_key,
        base_url=base_url_override or fallback_base,
        model=model_override or fallback_model,
        provider="custom",
        source="config",
    )


def get_ai_config(db: Session, config: Any) -> AIConfig:
    """AI 三件套生效配置（服务商引用 > 独立覆盖 > 默认值）。"""
    provider_name = get_config_value(db, "ai.provider", config) or "custom"
    return _resolve_ai_config(
        db,
        provider_name=provider_name,
        model_override=_get_override_value(db, "ai.model"),
        api_key_override=_get_override_value(db, "ai.api_key"),
        base_url_override=_get_override_value(db, "ai.base_url"),
        config=config,
    )


def list_provider_options(db: Session) -> list[str]:
    """服务商下拉选项：启用服务商 name + custom（供 ai.provider select）。"""
    names = [
        row[0]
        for row in db.query(AiProvider.name)
        .filter(AiProvider.enabled == 1)
        .order_by(AiProvider.sort_order, AiProvider.id)
        .all()
    ]
    return [*names, "custom"]


def _item_options(db: Session, item) -> Optional[list[str]]:
    """select 项选项：ai.provider 动态取服务商列表 + custom。"""
    if item.key == "ai.provider":
        return list_provider_options(db)
    return item.options


def _build_item(db: Session, key: str, config: Any = None) -> dict[str, Any]:
    """构造单个配置项响应（合并覆盖值 + 掩码 + 来源状态）。"""
    item = find_config_item(key, config or _load_config())
    if item is None:
        raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    default = item.default
    is_secret = item.value_type == VALUE_TYPE_SECRET

    if row is not None:
        effective = row.value or ""
        source = SOURCE_DB
    elif item.env_key is None:
        effective = default
        source = SOURCE_BUILTIN
    else:
        effective = default
        source = SOURCE_ENV

    return {
        "key": item.key,
        "category": item.category,
        "label": item.label,
        "description": item.description,
        "value_type": item.value_type,
        "editable": item.editable,
        "value": mask_secret(effective) if is_secret else effective,
        "has_value": bool(effective),
        "default_value": mask_secret(default) if is_secret else default,
        "source": source,
        "options": _item_options(db, item),
    }


def build_config_list(db: Session, config: Any = None) -> list[dict[str, Any]]:
    """全量配置项响应（供 GET /api/v1/config）。"""
    return [_build_item(db, item.key, config) for item in _registry_items(config)]


def _registry_items(config: Any = None) -> list:
    from app.config_registry import build_config_items

    return build_config_items(config or _load_config())
