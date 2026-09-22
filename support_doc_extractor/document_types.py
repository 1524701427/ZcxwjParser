"""支持性文件类型枚举与类型标准化工具。"""

from __future__ import annotations

from enum import Enum


class SupportDocType(str, Enum):
    """支持的单文件业务类型。"""

    LOAN_INTENT = "loan_intent"
    LAND_PREAPPROVAL = "land_preapproval"
    SOIL_WATER_APPROVAL = "soil_water_approval"
    ENVIRONMENT_APPROVAL = "environment_approval"
    GRID_ACCESS = "grid_access"

    @property
    def display_name(self) -> str:
        """获取 Java FileContent 使用的中文文件类型。\n\n        Returns:\n            Java 业务对象中的中文文件类型名称。\n        """
        return {
            SupportDocType.LOAN_INTENT: "贷款意向文件",
            SupportDocType.LAND_PREAPPROVAL: "用地预审文件",
            SupportDocType.SOIL_WATER_APPROVAL: "水保批复文件",
            SupportDocType.ENVIRONMENT_APPROVAL: "环保批复文件",
            SupportDocType.GRID_ACCESS: "接入批复文件",
        }[self]

    @property
    def short_name(self) -> str:
        """获取调用侧使用的中文简称。\n\n        Returns:\n            文件类型中文简称。\n        """
        return {
            SupportDocType.LOAN_INTENT: "贷款意向",
            SupportDocType.LAND_PREAPPROVAL: "用地预审",
            SupportDocType.SOIL_WATER_APPROVAL: "水保",
            SupportDocType.ENVIRONMENT_APPROVAL: "环评",
            SupportDocType.GRID_ACCESS: "接入",
        }[self]

    @classmethod
    def parse(cls, value: "SupportDocType | str") -> "SupportDocType":
        """将枚举或字符串别名转换为标准文件类型。\n\n        Args:\n            value: 文件类型枚举、中文别名或内部类型编码。\n\n        Returns:\n            标准化后的 SupportDocType。\n\n        Raises:\n            ValueError: 文件类型不受支持。\n        """
        if isinstance(value, cls):
            return value

        key = str(value or "").strip()
        aliases = {
            "贷款意向": cls.LOAN_INTENT,
            "贷款意向书": cls.LOAN_INTENT,
            "贷款意向文件": cls.LOAN_INTENT,
            "贷款": cls.LOAN_INTENT,
            "loan_intent": cls.LOAN_INTENT,
            "用地预审": cls.LAND_PREAPPROVAL,
            "用地预审文件": cls.LAND_PREAPPROVAL,
            "用地预审与选址意见书": cls.LAND_PREAPPROVAL,
            "land_preapproval": cls.LAND_PREAPPROVAL,
            "水保": cls.SOIL_WATER_APPROVAL,
            "水保批复": cls.SOIL_WATER_APPROVAL,
            "水保意见": cls.SOIL_WATER_APPROVAL,
            "水保报告": cls.SOIL_WATER_APPROVAL,
            "水保批复文件": cls.SOIL_WATER_APPROVAL,
            "soil_water_approval": cls.SOIL_WATER_APPROVAL,
            "环评": cls.ENVIRONMENT_APPROVAL,
            "环保": cls.ENVIRONMENT_APPROVAL,
            "环评批复": cls.ENVIRONMENT_APPROVAL,
            "环评意见": cls.ENVIRONMENT_APPROVAL,
            "环评报告": cls.ENVIRONMENT_APPROVAL,
            "环保批复文件": cls.ENVIRONMENT_APPROVAL,
            "environment_approval": cls.ENVIRONMENT_APPROVAL,
            "接入": cls.GRID_ACCESS,
            "接入批复": cls.GRID_ACCESS,
            "接入意见": cls.GRID_ACCESS,
            "接入系统批复": cls.GRID_ACCESS,
            "接入批复文件": cls.GRID_ACCESS,
            "grid_access": cls.GRID_ACCESS,
        }
        parsed = aliases.get(key)
        if parsed is not None:
            return parsed

        allowed = "、".join(item.short_name for item in cls)
        raise ValueError(f"不支持的文件类型: {value}。可用类型: {allowed}")


def normalize_doc_type(value: SupportDocType | str) -> str:
    """获取抽取配置使用的内部类型编码。\n\n    Args:\n        value: 文件类型枚举或字符串别名。\n\n    Returns:\n        内部类型编码。\n    """
    return SupportDocType.parse(value).value
