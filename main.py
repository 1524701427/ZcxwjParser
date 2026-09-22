"""FastAPI 服务入口。

提供单文件支持性文件解析接口。调用方上传一个 PDF，并指定文件类型，
服务完成解析后直接返回与 Java FileContent 对应的 JSON。
"""

from __future__ import annotations

import shutil
import tempfile
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from support_doc_extractor import SupportDocType, extract_document
from support_doc_extractor.logging_utils import get_logger


logger = get_logger("http")
app = FastAPI(
    title="支持性文件解析服务",
    version="1.0.0",
    description="上传单个 PDF 并指定文件类型，返回结构化 FileContent 数据。",
)


class ApiFileType(str, Enum):
    """HTTP 接口使用的文件类型枚举。"""

    LOAN_INTENT = "贷款意向"
    LAND_PREAPPROVAL = "用地预审"
    SOIL_WATER_APPROVAL = "水保"
    ENVIRONMENT_APPROVAL = "环评"
    GRID_ACCESS = "接入"

    def to_support_doc_type(self) -> SupportDocType:
        """转换为内部文件类型枚举。

        Returns:
            内部使用的 SupportDocType。
        """
        mapping = {
            ApiFileType.LOAN_INTENT: SupportDocType.LOAN_INTENT,
            ApiFileType.LAND_PREAPPROVAL: SupportDocType.LAND_PREAPPROVAL,
            ApiFileType.SOIL_WATER_APPROVAL: SupportDocType.SOIL_WATER_APPROVAL,
            ApiFileType.ENVIRONMENT_APPROVAL: SupportDocType.ENVIRONMENT_APPROVAL,
            ApiFileType.GRID_ACCESS: SupportDocType.GRID_ACCESS,
        }
        return mapping[self]


class FileContentResponse(BaseModel):
    """Java FileContent 对应的接口返回模型。"""

    fileType: str | None = None
    approvalUnit: str | None = None
    dispatchNo: str | None = None
    obtainDate: str | None = None
    loanRate: str | None = None
    landControl: str | None = None
    waterConservationInvestment: str | None = None
    soilWaterConservationFee: str | None = None
    environmentalProtectionInvestment: str | None = None
    accessScheme: str | None = None
    accessStationName: str | None = None
    accessLocation: str | None = None
    outletCircuitCount: str | None = None
    accessDistance: str | None = None
    outletConductorSection: str | None = None
    accessIntervalDescription: str | None = None
    mainTransformerCapacity: str | None = None
    mainTransformerWiringMode: str | None = None
    svgCapacity: str | None = None
    recognizeDate: str | None = None
    remark: str | None = None


@app.post(
    "/api/parse",
    response_model=FileContentResponse,
    summary="解析支持性文件",
)
async def parse_support_document(
    file: UploadFile = File(..., description="待解析的单个 PDF 文件"),
    file_type: ApiFileType = Form(..., description="文件类型"),
) -> FileContentResponse:
    """解析上传的单个支持性 PDF 文件。

    Args:
        file: 上传的 PDF 文件。
        file_type: 文件类型枚举，可选贷款意向、用地预审、水保、环评、接入。

    Returns:
        与 Java FileContent 字段完全对应的结构化结果。

    Raises:
        HTTPException: 文件类型、文件扩展名或解析过程存在异常。
    """
    filename = Path(file.filename or "upload.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="当前仅支持 PDF 文件。")

    try:
        with tempfile.TemporaryDirectory(prefix="support_doc_api_") as temp_dir:
            temp_root = Path(temp_dir)
            input_path = temp_root / filename
            cache_root = temp_root / "parsed"

            await run_in_threadpool(_save_upload_file, file.file, input_path)

            output = await run_in_threadpool(
                extract_document,
                file_type.to_support_doc_type(),
                input_path,
                parsed_root=cache_root,
                write_files=False,
            )
            return FileContentResponse(**output["result"])
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("api_parse_invalid file=%s error=%s", filename, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("api_parse_failed file=%s type=%s", filename, file_type.value)
        raise HTTPException(status_code=500, detail="文件解析失败，请查看服务日志。") from exc
    finally:
        await file.close()


def _save_upload_file(source: BinaryIO, target: Path) -> None:
    """将上传文件流保存到临时文件。

    Args:
        source: FastAPI UploadFile 对应的文件流。
        target: 临时文件目标路径。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    source.seek(0)
    with target.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
