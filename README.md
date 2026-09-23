# 支持性文件解析服务

项目使用 FastAPI 提供单文件解析接口。

## 启动

安装依赖：

```bash
pip install -r requirements.txt
```

启动服务：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

启动后可打开 Swagger：

```text
http://127.0.0.1:8000/docs
```

## 解析接口

```text
POST /api/parse
Content-Type: multipart/form-data
```

请求参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| file | File | 单个 PDF 或图片文件（PNG/JPG/TIFF/BMP） |
| file_type | Enum | 贷款意向 / 用地预审 / 水保 / 环评 / 接入 |

接口执行完成后直接返回与 Java `FileContent` 对应的 JSON，不返回临时文件路径。

示例返回：

```json
{
  "fileType": "环保批复文件",
  "title": "关于示例项目环境影响报告表的批复",
  "approvalUnit": "榆林市生态环境局",
  "dispatchNo": "榆环批复〔2026〕1号",
  "obtainDate": "2026-09-01T00:00:00",
  "loanRate": null,
  "landControl": null,
  "waterConservationInvestment": null,
  "soilWaterConservationFee": null,
  "environmentalProtectionInvestment": "500万元",
  "accessInvestment": null,
  "accessScheme": null,
  "accessStationName": null,
  "accessLocation": null,
  "outletCircuitCount": null,
  "accessDistance": null,
  "outletConductorSection": null,
  "accessIntervalDescription": null,
  "mainTransformerCapacity": null,
  "mainTransformerWiringMode": null,
  "svgCapacity": null,
  "recognizeDate": "2026-09-22T17:00:00",
  "remark": null
}
```

上传文件和本次解析缓存都放在临时目录，接口结束后自动删除，不会持续堆积 runtime 缓存。

## 目录结构

```text
.
├── main.py                    # FastAPI 服务入口
├── configs/                   # 解析和字段配置
├── support_doc_extractor/     # 解析实现
├── tests/
├── requirements.txt
└── pyproject.toml
```
