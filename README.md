# 支持性文件解析

项目只保留一个正式业务入口：传入一个文件 + 一个文件类型枚举，完成解析并输出结构化 JSON。

## 安装

```bash
pip install -r requirements.txt
```

运行时将源码目录加入 Python 路径。

Linux / macOS：

```bash
export PYTHONPATH=$PWD/support_doc_extractor/src
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="$PWD/support_doc_extractor/src"
```

## 唯一入口

正式入口文件：

```text
support_doc_extractor/src/support_doc_extractor/api.py
```

对外调用：

```python
from support_doc_extractor import SupportDocType, extract_document

output = extract_document(
    SupportDocType.ENVIRONMENT_APPROVAL,
    "/data/环评批复.pdf",
)
```

入口只接受单个文件，传目录会报错。

## 文件类型

```python
SupportDocType.LOAN_INTENT            # 贷款意向
SupportDocType.LAND_PREAPPROVAL       # 用地预审
SupportDocType.SOIL_WATER_APPROVAL    # 水保
SupportDocType.ENVIRONMENT_APPROVAL   # 环评
SupportDocType.GRID_ACCESS            # 接入
```

为兼容已有代码，仍支持 `"环评"`、`"水保批复"`、`"接入"` 等字符串；新代码统一使用枚举。

## 输出

输入 `/data/环评批复.pdf` 后生成：

```text
/data/环评批复.json
/data/环评批复_details.json
```

`环评批复.json` 是 Java `FileContent` 业务结果；`*_details.json` 保留识别来源、置信度、证据和告警。

## 代码结构

```text
support_doc_extractor/
├── configs/
│   ├── extraction.yaml
│   ├── fields.yaml
│   ├── parser.yaml
│   └── support_doc_types.yaml
└── src/support_doc_extractor/
    ├── __init__.py
    ├── api.py              # 唯一业务入口
    ├── document_types.py   # 文件类型枚举
    ├── engine.py           # 抽取流程
    ├── parsers.py          # PDF 解析与缓存
    ├── mappers.py          # Java FileContent 映射
    ├── normalizers.py      # 字段标准化
    ├── models.py           # 统一数据模型
    └── logging_utils.py    # 日志
```

## 日志

默认输出控制台，默认级别 `INFO`：

```bash
export SUPPORT_DOC_LOG_LEVEL=INFO
```

调试时设置为 `DEBUG`。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```
