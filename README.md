# 支持性文件解析

项目对外只保留一个最外层入口：`main.py` 中的 `parse_file()`。

调用方只需要传入：

- 一个实际文件路径
- 一个 `SupportDocType` 文件类型枚举

## 安装

```bash
pip install -r requirements.txt
```

## 最外层入口

```python
from main import SupportDocType, parse_file

result = parse_file(
    "/data/环评批复.pdf",
    SupportDocType.ENVIRONMENT_APPROVAL,
)
```

`parse_file()` 是业务方推荐调用的唯一入口，内部会依次完成：PDF 解析、OCR 兜底、字段抽取、结果标准化、Java `FileContent` 映射和 JSON 落盘。

## 文件类型

```python
SupportDocType.LOAN_INTENT            # 贷款意向
SupportDocType.LAND_PREAPPROVAL       # 用地预审
SupportDocType.SOIL_WATER_APPROVAL    # 水保
SupportDocType.ENVIRONMENT_APPROVAL   # 环评
SupportDocType.GRID_ACCESS            # 接入
```

## 输出

输入 `/data/环评批复.pdf` 后会生成：

```text
/data/环评批复.json
/data/环评批复_details.json
```

`*.json` 是 Java `FileContent` 业务结果；`*_details.json` 保存字段证据、置信度、解析器信息和告警。

## 目录结构

```text
.
├── main.py                    # 最外层正式入口
├── configs/                   # 解析和字段配置
│   ├── extraction.yaml
│   ├── fields.yaml
│   ├── parser.yaml
│   └── support_doc_types.yaml
├── support_doc_extractor/     # 内部实现包
│   ├── __init__.py
│   ├── api.py                 # 单文件业务 API
│   ├── document_types.py      # 文件类型枚举
│   ├── engine.py              # 抽取主流程
│   ├── parsers.py             # PDF 解析与缓存
│   ├── mappers.py             # Java FileContent 映射
│   ├── normalizers.py         # 字段标准化
│   ├── models.py              # 数据模型
│   └── logging_utils.py       # 日志
├── tests/
├── requirements.txt
└── pyproject.toml
```

## 代码规范

核心类和函数统一使用中文 Google 风格 Docstring：

```python
def parse_file(file_path: str, file_type: SupportDocType) -> dict:
    """解析单个支持性文件。

    Args:
        file_path: 待解析文件路径。
        file_type: 文件类型枚举。

    Returns:
        解析结果。

    Raises:
        FileNotFoundError: 文件不存在。
    """
```

## 日志

默认输出控制台，日志级别为 `INFO`。可通过环境变量修改：

```bash
export SUPPORT_DOC_LOG_LEVEL=DEBUG
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```
