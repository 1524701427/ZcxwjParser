from __future__ import annotations

from datetime import datetime
from typing import Any


FILE_TYPE_NAMES = {
    "loan_intent": "贷款意向文件",
    "land_preapproval": "用地预审文件",
    "soil_water_approval": "水保批复文件",
    "environment_approval": "环保批复文件",
    "grid_access": "接入批复文件",
    "unknown_support_doc": "未知支持性文件",
}


def _raw_value(details: dict[str, Any], field_name: str) -> Any:
    """Return the original extracted field value, preserving Java String semantics."""
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
    """Convert an extracted date to the ISO LocalDateTime form expected by Java."""
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

    # Normalizer emits YYYY-MM-DD. Java LocalDateTime needs a time component.
    if len(date_text) == 10 and date_text[4:5] == "-" and date_text[7:8] == "-":
        return f"{date_text}T00:00:00"

    # Already ISO-like LocalDateTime: keep it unchanged.
    if "T" in date_text:
        return date_text

    return None


def to_file_content(details: dict[str, Any]) -> dict[str, Any]:
    """Map internal extraction details to the Java FileContent JSON contract."""
    doc_type = str(details.get("doc_type") or "unknown_support_doc")
    land_control = _raw_value(details, "land_control") or _raw_value(details, "land_control_area")

    return {
        "fileType": FILE_TYPE_NAMES.get(doc_type, doc_type),
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
        "recognizeDate": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "remark": None,
    }
