"""字段值标准化与结果校验。"""

from __future__ import annotations

import re
from typing import Any

from support_doc_extractor.models import Document, ExtractedField

# ==== 字段标准化 ====

def normalize_field(field: ExtractedField) -> ExtractedField:
    """根据字段类型附加标准化结果。\n\n    Args:\n        field: 原始字段候选。\n\n    Returns:\n        已填充 normalized 的字段候选。\n    """
    text = str(field.value or "").strip()
    if not text:
        return field
    if field.name.endswith("investment") or "fee" in field.name or field.name == "total_investment":
        field.normalized = normalize_money(text)
    elif field.name in {"main_transformer_capacity"}:
        field.normalized = normalize_apparent_power(text)
    elif field.name in {"svg_capacity"}:
        field.normalized = normalize_reactive_power(text)
    elif field.name in {"line_length", "access_distance"}:
        field.normalized = normalize_length(text)
    elif field.name in {"outgoing_circuits"}:
        field.normalized = normalize_count(text, "\u56de")
    elif field.name in {"capacity"}:
        field.normalized = normalize_capacity(text)
    elif field.name in {"land_area", "land_control_area"}:
        field.normalized = normalize_area(text)
    elif field.name in {"issue_date"}:
        field.normalized = normalize_date(text)
    elif field.name in {"document_no"}:
        field.value = normalize_document_no(text)
    elif field.name in {"loan_interest_rate"}:
        field.normalized = normalize_percent(text)
    return field


def normalize_money(text: str) -> dict[str, Any] | None:
    """Normalize money expressions to ten-thousand yuan."""
    match = re.search(r"([0-9,.]+)\s*(\u4e07\u5143|\u4ebf\u5143|\u5143)", text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    if unit == "\u4ebf\u5143":
        return {"amount": amount * 10000, "unit": "\u4e07\u5143"}
    if unit == "\u5143":
        return {"amount": amount / 10000, "unit": "\u4e07\u5143"}
    return {"amount": amount, "unit": "\u4e07\u5143"}


def normalize_capacity(text: str) -> dict[str, Any] | None:
    """Normalize installed capacity to MW."""
    match = re.search(r"([0-9,.]+)\s*(MW|\u5146\u74e6|\u4e07\u5343\u74e6|kW)", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = match.group(2).lower()
    if unit == "\u4e07\u5343\u74e6":
        return {"value": value * 10, "unit": "MW"}
    if unit == "kw":
        return {"value": value / 1000, "unit": "MW"}
    return {"value": value, "unit": "MW"}


def normalize_area(text: str) -> dict[str, Any] | None:
    """Normalize land area to hectares."""
    match = re.search(r"([0-9,.]+)\s*(\u516c\u9877|\u4ea9|\u5e73\u65b9\u7c73|m2|\u33a1)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    if unit == "\u4ea9":
        return {"value": value / 15, "unit": "\u516c\u9877"}
    if unit in {"\u5e73\u65b9\u7c73", "m2", "\u33a1"}:
        return {"value": value / 10000, "unit": "\u516c\u9877"}
    return {"value": value, "unit": "\u516c\u9877"}


def normalize_date(text: str) -> str | None:
    """Normalize Chinese or slash-separated dates to ISO format."""
    match = re.search(r"((?:19|20)\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"((?:19|20)\d{2})[./-](\d{1,2})[./-](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def normalize_document_no(text: str) -> str:
    """Normalize bracket variants in official document numbers."""
    return (
        text.replace("\uff3b", "\u3014")
        .replace("[", "\u3014")
        .replace("\uff3d", "\u3015")
        .replace("]", "\u3015")
    )


def normalize_percent(text: str) -> dict[str, Any] | None:
    """Normalize percentage values."""
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not match:
        return None
    return {"value": float(match.group(1)), "unit": "%"}


def normalize_length(text: str) -> dict[str, Any] | None:
    """Normalize line distance to kilometers."""
    match = re.search(r"([0-9,.]+)(?:\s*[xX\u00d7]\s*([0-9,.]+))?\s*(km|\u516c\u91cc|\u5343\u7c73)", text, re.IGNORECASE)
    if not match:
        return None
    first = float(match.group(1).replace(",", ""))
    second = float(match.group(2).replace(",", "")) if match.group(2) else None
    value = first * second if second is not None else first
    return {"value": value, "unit": "km"}


def normalize_apparent_power(text: str) -> dict[str, Any] | None:
    """Normalize transformer apparent power to MVA."""
    match = re.search(r"([0-9,.]+)(?:\s*[xX\u00d7]\s*([0-9,.]+))?\s*(MVA|\u5146\u4f0f\u5b89)", text, re.IGNORECASE)
    if not match:
        return None
    first = float(match.group(1).replace(",", ""))
    second = float(match.group(2).replace(",", "")) if match.group(2) else None
    value = first * second if second is not None else first
    return {"value": value, "unit": "MVA"}


def normalize_reactive_power(text: str) -> dict[str, Any] | None:
    """Normalize reactive power to Mvar."""
    match = re.search(r"([0-9,.]+)\s*(Mvar|\u5146\u4e4f)", text, re.IGNORECASE)
    if not match:
        return None
    return {"value": float(match.group(1).replace(",", "")), "unit": "Mvar"}


def normalize_count(text: str, unit: str) -> dict[str, Any] | None:
    """Normalize integer counts while preserving the business unit."""
    match = re.search(r"([0-9]+)", text)
    if not match:
        return None
    return {"value": int(match.group(1)), "unit": unit}


# ==== 字段校验 ====

def validate_field(field: ExtractedField, document: Document) -> tuple[bool, str | None]:
    """Validate a candidate and return a warning when evidence is approximate."""
    if field.value is None or str(field.value).strip() == "":
        return False, "empty_value"
    text = document.full_text or document.rebuild_text()
    evidence = field.evidence or str(field.value)
    if evidence and evidence not in text and len(str(field.value)) > 4:
        return True, "value_not_exact_span"
    return True, None
