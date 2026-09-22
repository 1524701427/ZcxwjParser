# 支持性文件结构化抽取

这个目录是支持性文件的结构化抽取工程。调用时只需要传入文件类型和文件路径，程序会自动输出两个 JSON 文件。

## 当前架构

```text
PDF
  -> 文档加载与解析层
     OpenDataLoader / PyMuPDF
  -> 统一文档模型
     页面、标题、段落、表格、样式、坐标、图片信息
  -> 配置化抽取引擎
     配置文件 + 类型抽取 + 通用抽取 + 表格抽取 + 规则抽取
  -> 图片识别兜底
     OpenDataLoader 混合识别 / RapidOCR 图片块识别
  -> 候选合并、选择、标准化、派生计算
  -> 结果文件
```

## 目录说明

```text
support_doc_extractor/
  configs/
    parser.yaml             # 解析器和图片识别参数
    support_doc_types.yaml  # 文档分类关键词和字段清单
    fields.yaml             # 字段别名、单位、值类型、抽取器标签
    extraction.yaml         # 抽取器顺序、开关、候选优先级
  src/support_doc_extractor/
    __init__.py             # 对外入口
    models.py               # 统一模型、配置路径、配置读取
    parsers.py              # PyMuPDF/OpenDataLoader 解析、缓存与表格适配
    normalizers.py          # 字段标准化与校验
    engine.py               # 分类、抽取、OCR 调度、候选合并、业务入口
```

## 调用方式

只有一种调用方式：传入类型、文件。

```python
import sys

sys.path.insert(0, r"support_doc_extractor/src")

from support_doc_extractor import extract_document

output = extract_document(
    "环评批复",
    r"支持性文件/示例.pdf",
)

print(output["result_path"])    # 支持性文件/示例.json
print(output["details_path"])   # 支持性文件/示例_details.json
```

第一个参数是文件类型，支持：

- `贷款意向书`
- `用地预审`
- `环评批复`
- `环评意见`
- `环评报告`
- `水保批复`
- `水保意见`
- `水保报告`
- `接入批复`

也可以传内部类型编码：

- `loan_intent`
- `land_preapproval`
- `environment_approval`
- `soil_water_approval`
- `grid_access`

## 输出文件

不需要手动传输出路径。假设输入文件是：

```text
支持性文件/示例.pdf
```

程序会自动生成：

```text
支持性文件/示例.json
支持性文件/示例_details.json
```

`示例.json` 是业务用的简洁结果：

```python
{
    "file": "文件路径",
    "doc_type": "environment_approval",
    "fields": {
        "document_no": "文号",
        "title": "文书标题",
        "environmental_investment": {
            "amount": 382.0,
            "unit": "万元"
        }
    }
}
```

`示例_details.json` 是详细结果，包含原始值、标准化值、来源、置信度、证据、表格、告警等信息。

调用函数也会返回这些路径和内容：

```python
{
    "result_path": "支持性文件/示例.json",
    "details_path": "支持性文件/示例_details.json",
    "result": {},
    "details": {}
}
```

详细结果结构：

```python
{
    "file": "文件路径",
    "doc_type": "environment_approval",
    "fields": {},
    "tables": [],
    "warnings": [],
    "meta": {}
}
```

## 已配置字段

通用字段：

- 文号：`document_no`
- 文书标题：`title`
- 批准单位名称：`approval_agency`
- 取得时间：`issue_date`

专项字段：

- 贷款意向书：贷款利率
- 用地预审：用地控制指标
- 环评批复：环境保护投资
- 水保批复：水土保持投资、水土保持补偿费
- 接入批复：接入投资、接入方案、接入站名称、送出回路数、接入距离、送出导线截面、接入间隔说明、主变容量、主变接线方式、SVG 容量

## 配置方式

新增字段时优先改配置：

1. 在 `configs/fields.yaml` 增加字段别名、单位、值类型和抽取器标签。
2. 在 `configs/support_doc_types.yaml` 把字段加入对应文档类型。
3. 简单关键词字段在 `src/support_doc_extractor/engine.py` 的规则配置里增加正则。
4. 版式固定字段在 `src/support_doc_extractor/engine.py` 的类型抽取逻辑里增加处理。

OpenDataLoader 解析缓存放在 `runtime/parsed/`，按源文件路径、大小和修改时间隔离；业务 JSON 结果默认写在输入文件旁边。`runtime/` 可以删除后重新生成。
