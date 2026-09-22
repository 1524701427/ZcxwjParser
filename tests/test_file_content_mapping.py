from support_doc_extractor.mappers import to_file_content


def _field(value, normalized=None):
    return {
        "value": value,
        "source": "rule",
        "confidence": 0.9,
        "page": 1,
        "evidence": value,
        "normalized": normalized,
        "meta": {},
    }


def test_java_file_content_mapping_uses_raw_business_strings():
    details = {
        "doc_type": "grid_access",
        "fields": {
            "approval_agency": _field("国网陕西省电力有限公司"),
            "document_no": _field("陕电发展〔2026〕12号"),
            "issue_date": _field("2026年9月1日", "2026-09-01"),
            "access_plan": _field("接入110kV示例变电站"),
            "access_station": _field("110kV示例变电站"),
            "access_location": _field("西安市某区"),
            "outgoing_circuits": _field("1回", {"value": 1, "unit": "回"}),
            "access_distance": _field("5.6km", {"value": 5.6, "unit": "km"}),
            "main_transformer_capacity": _field("50MVA", {"value": 50, "unit": "MVA"}),
        },
    }

    result = to_file_content(details)

    assert result["fileType"] == "接入批复文件"
    assert result["approvalUnit"] == "国网陕西省电力有限公司"
    assert result["dispatchNo"] == "陕电发展〔2026〕12号"
    assert result["obtainDate"] == "2026-09-01T00:00:00"
    assert result["accessScheme"] == "接入110kV示例变电站"
    assert result["accessLocation"] == "西安市某区"
    assert result["outletCircuitCount"] == "1回"
    assert result["accessDistance"] == "5.6km"
    assert result["mainTransformerCapacity"] == "50MVA"
    assert result["remark"] is None
    assert result["recognizeDate"]


def test_land_control_prefers_text_semantics_over_area_fallback():
    details = {
        "doc_type": "land_preapproval",
        "fields": {
            "land_control": _field("不占用永久基本农田"),
            "land_control_area": _field("12.5公顷", {"value": 12.5, "unit": "公顷"}),
        },
    }
    assert to_file_content(details)["landControl"] == "不占用永久基本农田"


def test_all_java_contract_keys_are_stable_when_values_missing():
    result = to_file_content({"doc_type": "loan_intent", "fields": {}})
    assert list(result) == [
        "fileType",
        "approvalUnit",
        "dispatchNo",
        "obtainDate",
        "loanRate",
        "landControl",
        "waterConservationInvestment",
        "soilWaterConservationFee",
        "environmentalProtectionInvestment",
        "accessScheme",
        "accessStationName",
        "accessLocation",
        "outletCircuitCount",
        "accessDistance",
        "outletConductorSection",
        "accessIntervalDescription",
        "mainTransformerCapacity",
        "mainTransformerWiringMode",
        "svgCapacity",
        "recognizeDate",
        "remark",
    ]
    assert result["loanRate"] is None
