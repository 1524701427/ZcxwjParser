"""Java FileContent 业务结果映射。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from support_doc_extractor.logging_utils import get_logger
from support_doc_extractor.document_types import SupportDocType

logger = get_logger("mappers")


def _raw_value(details: dict[str, Any], field_name: str) -> Any:
    """读取字段原始值并保持 Java String 语义。\n\n    Args:\n        details: 详细抽取结果。\n        field_name: 内部字段名。\n\n    Returns:\n        原始字符串值；字段不存在时返回 None。\n    """
    item = (details.get("fields") or {}).get(field_name)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _local_datetime_value(details: dict[str, Any], field_name: str) -> str | None:
    """将日期转换为 Java LocalDateTime 可反序列化格式。\n\n    Args:\n        details: 详细抽取结果。\n        field_name: 日期字段名。\n\n    Returns:\n        ISO LocalDateTime 字符串；无法转换时返回 None。\n    """
    item = (details.get("fields") or {}).get(field_name)
    if not isinstance(item, dict):
        return None

    normalized = item.get("normalized")
    if isinstance(normalized, str) and normalized:
        date_text = normalized.strip()
    else:
        raw = item.get("value")
        date_text = str(raw or "").strip()

    if not date_text:
        return None

    # 标准化日期通常为 YYYY-MM-DD，Java LocalDateTime 需要补充时间部分。
    if len(date_text) == 10 and date_text[4:5] == "-" and date_text[7:8] == "-":
        return f"{date_text}T00:00:00"

    # 已经是 ISO LocalDateTime 格式时直接保留。
    if "T" in date_text:
        return date_text

    return None


def to_file_content(details: dict[str, Any]) -> dict[str, Any]:
    """将内部抽取结果映射为 Java FileContent 字段。\n\n    Args:\n        details: ExtractionResult.to_dict() 生成的详细结果。\n\n    Returns:\n        与 Java FileContent 字段名一致的扁平字典。\n    """
    doc_type = str(details.get("doc_type") or "unknown_support_doc")
    land_control = _raw_value(details, "land_control") or _raw_value(details, "land_control_area")

    result = {
        "fileType": SupportDocType.parse(doc_type).display_name if doc_type != "unknown_support_doc" else "未知支持性文件",
        "approvalUnit": _raw_value(details, "approval_agency"),
        "dispatchNo": _raw_value(details, "document_no"),
        "obtainDate": _local_datetime_value(details, "issue_date"),
        "loanRate": _raw_value(details, "loan_interest_rate"),
        "landControl": land_control,
        "waterConservationInvestment": _raw_value(details, "soil_water_investment"),
        "soilWaterConservationFee": _raw_value(details, "soil_water_compensation_fee"),
        "environmentalProtectionInvestment": _raw_value(details, "environmental_investment"),
        "accessScheme": _raw_value(details, "access_plan"),
        "accessStationName": _raw_value(details, "access_station"),
        "accessLocation": _raw_value(details, "access_location"),
        "outletCircuitCount": _raw_value(details, "outgoing_circuits"),
        "accessDistance": _raw_value(details, "access_distance"),
        "outletConductorSection": _raw_value(details, "conductor_section"),
        "accessIntervalDescription": _raw_value(details, "access_interval_desc"),
        "mainTransformerCapacity": _raw_value(details, "main_transformer_capacity"),
        "mainTransformerWiringMode": _raw_value(details, "main_transformer_wiring"),
        "svgCapacity": _raw_value(details, "svg_capacity"),
        "recognizeDate": datetime.now().replace(microsecond=0).isoformat(),
        "remark": None,
    }
    mapped_count = sum(1 for key, value in result.items() if value is not None and key not in {"recognizeDate"})
    logger.info(
        "java_mapping_done doc_type=%s populated_fields=%d total_fields=%d",
        doc_type,
        mapped_count,
        len(result),
    )
    logger.debug("java_mapping_fields populated=%s", sorted(key for key, value in result.items() if value is not None))
    return result
