from __future__ import annotations

import json
import re
import fitz
import cv2
import numpy as np
from collections import Counter
from pathlib import Path
from typing import Any, Optional, List, Union

import opendataloader_pdf


CHILD_KEYS = ("kids", "list items", "rows", "cells")


def iter_elements(node: Any):
    """
    递归遍历 PDF 解析结果中的所有结构元素。

    opendataloader-pdf 的 JSON 中，普通段落通常放在 kids 中，
    列表和表格还可能放在 list items、rows、cells 中，所以这里统一递归处理。

    Args:
        node: JSON 根节点、子节点或节点列表。

    Yields:
        带有 type 字段的结构元素字典。
    """
    if isinstance(node, dict):
        if "type" in node:
            yield node

        for key in CHILD_KEYS:
            child = node.get(key)
            if child:
                yield from iter_elements(child)

    elif isinstance(node, list):
        for item in node:
            yield from iter_elements(item)


def load_json_result(output_dir: Path, pdf_path: Path) -> dict[str, Any]:
    """
    加载 PDF 解析生成的 JSON 结果文件。

    Args:
        output_dir: 当前 PDF 对应的输出目录。
        pdf_path: 原始 PDF 文件路径，用于优先匹配同名 JSON。

    Returns:
        解析后的 JSON 字典。
    """
    expected = output_dir / f"{pdf_path.stem}.json"
    if expected.exists():
        return json.loads(expected.read_text(encoding="utf-8"))

    json_files = sorted(output_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"在 {output_dir} 中未找到 JSON 输出文件")
    return json.loads(json_files[0].read_text(encoding="utf-8"))


def clean_text(text: Any) -> str:
    """
    清理文本中的多余空白，保留原始语义内容。

    Args:
        text: 原始文本或可转为字符串的值。

    Returns:
        合并连续空白后的文本。
    """
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_folder_name(name: str) -> str:
    """
    将 PDF 文件名转换为可用的输出文件夹名称。

    Args:
        name: PDF 文件名或其他目录名候选。

    Returns:
        去掉 Windows 非法字符后的文件夹名称。
    """
    folder_name = clean_text(name)
    folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", folder_name)
    folder_name = folder_name.strip(" .")
    return folder_name or "未命名PDF"


def get_bbox(element: dict[str, Any]) -> Optional[list[float]]:
    """
    获取元素坐标框，格式为 [x0, y0, x1, y1]。

    Args:
        element: opendataloader 输出中的结构元素。

    Returns:
        坐标框列表；没有合法坐标时返回 None。
    """
    bbox = element.get("bounding box")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None

    try:
        return [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None


def collect_text_elements(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """
    收集带有文本、页码和坐标的元素。

    Args:
        doc: opendataloader 输出的完整 JSON。

    Returns:
        已清洗并排除明显干扰项的文本元素列表。
    """
    elements = []
    for element in iter_elements(doc):
        content = clean_text(element.get("content"))
        bbox = get_bbox(element)
        page_number = element.get("page number")

        if not content or bbox is None or page_number is None:
            continue
        if is_noise_text(content):
            continue

        item = dict(element)
        item["content"] = content
        item["bounding box"] = bbox
        elements.append(item)

    return elements


def page_extent(elements: list[dict[str, Any]], page_number: int) -> Optional[list[float]]:
    """
    根据当前页已有元素估算页面内容范围。

    JSON 中没有直接给出页面宽高时，用所有元素的坐标范围做归一化参照。

    Args:
        elements: 已收集的文本元素列表。
        page_number: 需要估算范围的页码。

    Returns:
        当前页内容外接矩形；没有元素时返回 None。
    """
    boxes = [element["bounding box"] for element in elements if element.get("page number") == page_number]
    if not boxes:
        return None

    xs = [value for box in boxes for value in (box[0], box[2])]
    ys = [value for box in boxes for value in (box[1], box[3])]
    return [min(xs), min(ys), max(xs), max(ys)]


def normalized_center(bbox: list[float], extent: list[float]) -> tuple[float, float]:
    """
    返回坐标框中心点在页面内容范围中的位置。

    x_ratio 越接近 0.5 越居中；y_ratio 越小越靠上，越大越靠下。

    Args:
        bbox: 元素坐标框。
        extent: 当前页内容范围。

    Returns:
        中心点横向比例和纵向比例。
    """
    x0, y0, x1, y1 = bbox
    min_x, min_y, max_x, max_y = extent
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)

    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2
    x_ratio = (center_x - min_x) / width

    # 当前 JSON 的 y 坐标越大越靠上，所以用 max_y 减中心点得到从上到下的比例。
    y_ratio = (max_y - center_y) / height
    return x_ratio, y_ratio


def bbox_union(elements: list[dict[str, Any]]) -> Optional[list[float]]:
    """
    合并多个元素的坐标框。

    Args:
        elements: 待合并的元素列表。

    Returns:
        合并后的外接矩形；没有有效坐标时返回 None。
    """
    boxes = [element["bounding box"] for element in elements if get_bbox(element)]
    if not boxes:
        return None

    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def is_page_number_text(text: str) -> bool:
    """
    判断是否为页码文本，用于过滤页脚干扰。

    Args:
        text: 待判断文本。

    Returns:
        是页码文本返回 True，否则返回 False。
    """
    return bool(re.fullmatch(r"[-－—]?\s*\d+\s*[-－—]?", text))


def is_noise_text(text: str) -> bool:
    '''
    判断文本是否为水印、页码、装订提示等干扰项。

    Args:
        text: 待判断的原始文本。

    Returns:
        如果文本不应参与标题、发文单位、表格识别，返回 True；否则返回 False。
    '''
    normalized = clean_text(text)
    if not normalized:
        return True
    if is_page_number_text(normalized):
        return True

    compact = re.sub(r'\s+', '', normalized)
    noise_keywords = (
        '严禁外传',
        '专用严禁',
        '内部资料',
        '仅供参考',
        '扫描全能王',
        'CamScanner',
    )
    return any(keyword in compact for keyword in noise_keywords)


def make_region(name: str, elements: list[dict[str, Any]], region_bbox: Optional[list[float]] = None) -> dict[str, Any]:
    """
    将若干元素整理为统一的抽取结果。

    Args:
        name: 区域名称，例如 title、signature。
        elements: 构成该区域的元素列表。
        region_bbox: 可选的扩展区域坐标。

    Returns:
        包含文本、页码、坐标和来源元素信息的区域字典。
    """
    ordered = sorted(elements, key=lambda item: (item.get("page number", 0), -item["bounding box"][3], item["bounding box"][0]))
    text = "\n".join(element["content"] for element in ordered)
    element_bbox = bbox_union(ordered)

    return {
        "name": name,
        "page_number": ordered[0].get("page number") if ordered else None,
        "text": text,
        "bbox": element_bbox,
        "region_bbox": region_bbox or element_bbox,
        "source_ids": [element.get("id") for element in ordered if element.get("id") is not None],
        "source_types": [element.get("type") for element in ordered],
    }


def pick_title(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    通过版面特征抽取公文标题。

    标题通常位于第一页上半部分，字号较大，位置居中，解析类型也常为 heading。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        标题区域结果；无法判断时返回 None。
    """
    if not elements:
        return None

    first_page = min(int(element["page number"]) for element in elements)
    extent = page_extent(elements, first_page)
    if extent is None:
        return None

    candidates = []
    for element in elements:
        if element.get("page number") != first_page:
            continue

        text = element["content"]
        if is_page_number_text(text):
            continue

        bbox = element["bounding box"]
        x_ratio, y_ratio = normalized_center(bbox, extent)
        font_size = float(element.get("font size") or 0)
        element_type = str(element.get("type") or "")

        # 公文标题通常不会贴到页面最顶端，也不会进入正文下半区。
        if not (0.08 <= y_ratio <= 0.45):
            continue

        score = font_size
        score += max(0, 12 - abs(x_ratio - 0.5) * 24)
        if element_type == "heading":
            score += 25
        if len(text) >= 12:
            score += 5

        candidates.append((score, element))

    if not candidates:
        return None

    title = max(candidates, key=lambda item: item[0])[1]
    return make_region("title", [title])


def find_attachment_start_page(elements: list[dict[str, Any]]) -> Optional[int]:
    """
    查找附件正文开始页。

    这里不是用附件名称做强匹配，只判断页面顶部是否出现“附件”开头的短文本。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        附件开始页页码；未找到时返回 None。
    """
    pages = sorted({int(element["page number"]) for element in elements})
    if not pages:
        return None

    first_page = pages[0]

    for page_number in pages:
        if page_number == first_page:
            continue

        extent = page_extent(elements, page_number)
        if extent is None:
            continue

        for element in elements:
            if element.get("page number") != page_number:
                continue

            text = element["content"]
            _, y_ratio = normalized_center(element["bounding box"], extent)
            if y_ratio <= 0.25 and text.startswith("附件") and len(text) <= 20:
                return page_number

    return None


def pick_signature(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    抽取正文末尾的落款区域。

    逻辑重点是“正文结束页 + 页面右下区域”，不依赖固定文件名称或固定正文内容。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        落款区域结果；未找到时返回 None。
    """
    if not elements:
        return None

    pages = sorted({int(element["page number"]) for element in elements})
    attachment_start_page = find_attachment_start_page(elements)
    target_page = attachment_start_page - 1 if attachment_start_page and attachment_start_page > pages[0] else pages[-1]
    extent = page_extent(elements, target_page)
    if extent is None:
        return None

    candidates = []
    for element in elements:
        if element.get("page number") != target_page:
            continue

        text = element["content"]
        if is_page_number_text(text):
            continue

        x_ratio, y_ratio = normalized_center(element["bounding box"], extent)
        font_size = float(element.get("font size") or 0)

        # 落款通常在页面下方偏右，字号接近正文。
        if y_ratio < 0.58 or x_ratio < 0.42 or font_size < 8:
            continue

        score = font_size
        score += y_ratio * 20
        score += x_ratio * 12
        if any(keyword in text for keyword in ("年", "月", "日")):
            score += 10
        if any(keyword in text for keyword in ("委员会", "政府", "局", "公司", "办公室", "发展和改革")):
            score += 8

        candidates.append((score, element))

    if not candidates:
        return None

    best = max(candidates, key=lambda item: item[0])[1]
    best_bbox = best["bounding box"]
    _, best_y_ratio = normalized_center(best_bbox, extent)

    # 如果落款被拆成多行，把同一右下区域内、纵向距离接近的元素一起合并。
    grouped = []
    best_top = best_bbox[3]
    for _, element in candidates:
        x_ratio, y_ratio = normalized_center(element["bounding box"], extent)
        element_top = element["bounding box"][3]
        vertical_distance = abs(element_top - best_top)
        if x_ratio >= 0.40 and abs(y_ratio - best_y_ratio) <= 0.16 and vertical_distance <= 70:
            grouped.append(element)

    text_bbox = bbox_union(grouped)

    # 印章常覆盖在落款左侧或中间，区域框适当向左、上下扩展，方便后续裁剪或截图验证。
    region_bbox = None
    if text_bbox:
        region_bbox = [text_bbox[0] - 90, text_bbox[1] - 35, text_bbox[2] + 25, text_bbox[3] + 35]

    return make_region("signature", grouped, region_bbox=region_bbox)


TITLE_KEYWORDS = ("关于", "通知", "批复", "意见", "方案", "公告", "决定", "纪要", "函", "请示", "报告")
ISSUER_SUFFIXES = ("委员会", "人民政府", "政府", "办公室", "厅", "局", "公司", "集团", "中心", "管委会")
ISSUER_FIELD_LABELS = ("责任单位", "建设单位", "申报单位", "项目单位", "承诺单位", "填报单位")
ISSUER_FRAGMENT_PREFIXES = ("有限", "责任", "技术咨询", "经济技术", "咨询", "分公司", "公司", "办公室")


def normalize_field_text(text: str) -> str:
    """
    归一化字段文本，减少 OCR 标点差异对判断的影响。

    Args:
        text: 待归一化文本。

    Returns:
        统一空白和常见中英文括号后的文本。
    """
    text = clean_text(text)
    replacements = {
        "［": "〔",
        "[": "〔",
        "］": "〕",
        "]": "〕",
        "【": "〔",
        "】": "〕",
        "（": "(",
        "）": ")",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def clean_issuer_text(text: str) -> str:
    """
    清洗发文单位文本，去掉日期等落款附加信息。

    Args:
        text: 发文单位候选文本。

    Returns:
        去掉日期、“文件”等附加词后的发文单位名称。
    """
    text = normalize_field_text(text)
    text = re.sub(r"\s*\d{4}\s*年.*$", "", text).strip()
    text = re.sub(r"\s*\d{4}[./-]\d{1,2}[./-]\d{1,2}.*$", "", text).strip()
    return text.removesuffix("文件").strip()


def looks_like_issuer(text: str) -> bool:
    """
    判断文本是否像发文单位名称。

    Args:
        text: 发文单位候选文本。

    Returns:
        满足长度、后缀、非收文对象等条件时返回 True。
    """
    text = clean_issuer_text(text)
    if not 4 <= len(text) <= 40:
        return False
    if text.startswith(('各', '有关', '相关')):
        return False
    if any(keyword in text for keyword in ISSUER_FIELD_LABELS):
        return False
    if text.startswith(ISSUER_FRAGMENT_PREFIXES):
        return False
    if text.endswith(('：', ':')):
        return False
    if any(keyword in text for keyword in TITLE_KEYWORDS):
        return False
    return any(suffix in text for suffix in ISSUER_SUFFIXES)


def issuer_evidence_keywords(issuer_text: str) -> list[str]:
    '''
    从完整发文单位中生成用于章附近残片校验的通用关键词。

    Args:
        issuer_text: 已确认的完整发文单位。

    Returns:
        可用于匹配 OCR 残片的关键词列表。
    '''
    compact = re.sub(r'\s+', '', normalize_field_text(issuer_text))
    if not compact:
        return []

    keywords = {suffix for suffix in ISSUER_SUFFIXES if suffix in compact}
    for size in (4, 3, 2):
        for index in range(0, max(len(compact) - size + 1, 0)):
            piece = compact[index:index + size]
            if any(label in piece for label in ISSUER_FIELD_LABELS):
                continue
            if piece in TITLE_KEYWORDS:
                continue
            keywords.add(piece)

    return sorted(keywords, key=lambda item: (-len(item), item))


def strip_issuer_from_title(title: str, issuer: Optional[str]) -> str:
    """
    从标题中去掉发文单位前缀。

    Args:
        title: 标题候选文本。
        issuer: 已识别的发文单位，可为空。

    Returns:
        去掉发文单位前缀后的标题文本。
    """
    title = normalize_field_text(title)
    issuer = normalize_field_text(issuer or "")

    if issuer and title.startswith(issuer):
        title = title[len(issuer):].strip()

    # 很多公文标题从“关于”开始；如果 OCR 或解析把发文单位拼进标题，可以用“关于”作为标题起点。
    about_index = title.find("关于")
    if about_index > 0:
        prefix = title[:about_index].strip()
        if looks_like_issuer(prefix):
            title = title[about_index:].strip()

    return title


def make_field_result(
    name: str,
    text: str,
    source: str,
    element: Optional[dict[str, Any]] = None,
    confidence: float = 0.0,
) -> dict[str, Any]:
    """
    生成字段抽取结果。

    Args:
        name: 字段名称，例如 title、issuer。
        text: 字段文本。
        source: 字段来源说明。
        element: 字段来源元素，可为空。
        confidence: 规则打分，仅用于排序和调试。

    Returns:
        标准化字段结果字典。
    """
    if name == "issuer":
        text = clean_issuer_text(text)
    return {
        "name": name,
        "text": normalize_field_text(text),
        "source": source,
        "confidence": round(confidence, 3),
        "page_number": element.get("page number") if element else None,
        "bbox": element.get("bounding box") if element else None,
        "source_id": element.get("id") if element else None,
        "source_type": element.get("type") if element else None,
    }


def top_page_elements(elements: list[dict[str, Any]], page_number: int, max_y_ratio: float = 0.55) -> list[dict[str, Any]]:
    """
    取某页上半区元素，并按视觉顺序排序。

    Args:
        elements: 已过滤干扰项的文本元素列表。
        page_number: 目标页码。
        max_y_ratio: 纵向比例上限，越小越靠近页首。

    Returns:
        按从上到下、从左到右排序的元素列表。
    """
    extent = page_extent(elements, page_number)
    if extent is None:
        return []

    result = []
    for element in elements:
        if element.get("page number") != page_number:
            continue
        _, y_ratio = normalized_center(element["bounding box"], extent)
        if y_ratio <= max_y_ratio:
            item = dict(element)
            item["_y_ratio"] = y_ratio
            result.append(item)

    return sorted(result, key=lambda item: (item["_y_ratio"], item["bounding box"][0]))


def get_pdf_page_sizes(pdf_path: Path) -> dict[int, tuple[float, float]]:
    """
    获取 PDF 每页宽高。

    Args:
        pdf_path: PDF 文件路径。

    Returns:
        页码到宽高元组的映射。
    """
    sizes = {}
    with fitz.open(pdf_path) as pdf_doc:
        for index, page in enumerate(pdf_doc, 1):
            rect = page.rect
            sizes[index] = (float(rect.width), float(rect.height))
    return sizes


def find_red_stamp_regions(pdf_path: Path, page_numbers: list[int], scale: float = 2.0) -> list[dict[str, Any]]:
    """
    在指定页中检测红色印章区域。

    返回的 bbox 使用 PDF 坐标系：[x0, y0, x1, y1]，其中 y 从页面顶部向下。

    Args:
        pdf_path: PDF 文件路径。
        page_numbers: 需要检测红章的页码列表。
        scale: 渲染缩放倍数，越大检测越细但越慢。

    Returns:
        红章候选区域列表，已过滤红头文字等小红色块。
    """
    regions = []
    with fitz.open(pdf_path) as pdf_doc:
        for page_number in page_numbers:
            if page_number < 1 or page_number > pdf_doc.page_count:
                continue

            page = pdf_doc[page_number - 1]
            matrix = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            rgb = image
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)

            # 红章通常落在两个 hue 区间，同时要求饱和度和亮度足够高。
            lower_red_1 = np.array([0, 60, 80])
            upper_red_1 = np.array([12, 255, 255])
            lower_red_2 = np.array([165, 60, 80])
            upper_red_2 = np.array([180, 255, 255])
            mask = cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(hsv, lower_red_2, upper_red_2)

            kernel = np.ones((5, 5), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 200:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                if w < 20 or h < 20:
                    continue

                width = w / scale
                height = h / scale
                region_area = area / (scale * scale)
                aspect_ratio = width / max(height, 1.0)
                if width < 45 or height < 45:
                    continue
                if region_area < 1500:
                    continue
                if not 0.55 <= aspect_ratio <= 1.8:
                    continue

                # 首页上方的红头文字也会形成红色连通块，需要排除，避免误当成印章。
                center_y = (y + h / 2) / scale
                if page_number == 1 and center_y / max(float(page.rect.height), 1.0) < 0.34:
                    continue

                bbox = [x / scale, y / scale, (x + w) / scale, (y + h) / scale]
                regions.append(
                    {
                        "page_number": page_number,
                        "bbox": bbox,
                        "area": float(area / (scale * scale)),
                    }
                )
    return regions


def pdf_bbox_to_top_down_bbox(bbox: list[float], page_height: float, already_top_down: bool = False) -> list[float]:
    """
    将 JSON bbox 转成 PyMuPDF 图像坐标系。

    opendataloader 原生解析通常是 y 为负值；hybrid/docling 可能已经是自上而下正值。

    Args:
        bbox: JSON 元素坐标框。
        page_height: 当前 PDF 页真实高度。
        already_top_down: 坐标是否已经是自页面顶部向下，PyMuPDF 文本块传 True。

    Returns:
        自页面顶部向下计数的坐标框。
    """
    x0, y0, x1, y1 = bbox
    if already_top_down:
        return [x0, y0, x1, y1]
    if y0 < 0 or y1 < 0:
        return [x0, page_height + y0, x1, page_height + y1]
    return [x0, page_height - y1, x1, page_height - y0]


def collect_pymupdf_text_elements(pdf_path: Path, page_numbers: list[int]) -> list[dict[str, Any]]:
    '''
    从 PDF 原生文本层读取指定页的文本块。

    Args:
        pdf_path: PDF 文件路径。
        page_numbers: 需要读取文本块的页码列表。

    Returns:
        与 opendataloader 文本元素结构兼容的元素列表，坐标为自上而下的 PDF 坐标。
    '''
    elements: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as pdf_doc:
        for page_number in page_numbers:
            if page_number < 1 or page_number > pdf_doc.page_count:
                continue
            page = pdf_doc[page_number - 1]
            for block_index, block in enumerate(page.get_text('blocks'), 1):
                if len(block) < 5:
                    continue
                x0, y0, x1, y1, text = block[:5]
                content = clean_text(text)
                if is_noise_text(content):
                    continue
                elements.append(
                    {
                        'id': f'pymupdf-{page_number}-{block_index}',
                        'type': 'pymupdf_block',
                        'content': content,
                        'page number': page_number,
                        'bounding box': [float(x0), float(y0), float(x1), float(y1)],
                    }
                )
    return elements


def pick_stamp_nearby_issuer_from_regions(stamp_regions: list[dict[str, Any]], elements: list[dict[str, Any]], page_sizes: dict[int, tuple[float, float]]) -> Optional[dict[str, Any]]:
    """
    根据已检测到的印章区域，在印章附近提取发文单位。

    Args:
        stamp_regions: 红章候选区域列表。
        elements: 已过滤干扰项的文本元素列表。
        page_sizes: PDF 页码到真实宽高的映射。

    Returns:
        印章附近最可信的发文单位；没有匹配时返回 None。
    """
    candidates = []
    for stamp in stamp_regions:
        page_number = stamp["page_number"]
        page_size = page_sizes.get(page_number)
        if not page_size:
            continue
        page_height = page_size[1]

        sx0, sy0, sx1, sy1 = stamp["bbox"]
        stamp_cx = (sx0 + sx1) / 2
        for element in elements:
            if element.get("page number") != page_number:
                continue
            text = normalize_field_text(element["content"])
            if not looks_like_issuer(text):
                continue
            if "印发" in text:
                continue

            eb = pdf_bbox_to_top_down_bbox(element["bounding box"], page_height, element.get('type') == 'pymupdf_block')
            ex0, ey0, ex1, ey1 = eb
            ecx = (ex0 + ex1) / 2

            # 兼容三种情况：文字在章上方、章下方，或与章重叠。
            element_center_y = (ey0 + ey1) / 2
            stamp_center_y = (sy0 + sy1) / 2
            vertical_distance = abs(element_center_y - stamp_center_y)
            vertical_ok = vertical_distance <= max(260, (sy1 - sy0) * 2.2)
            horizontal_ok = abs(ecx - stamp_cx) <= max(180, (sx1 - sx0) * 1.8)
            if not vertical_ok or not horizontal_ok:
                continue

            score = 0.75
            score += min(stamp.get("area", 0) / 5000, 0.25)
            score += max(0, 0.15 - abs(ecx - stamp_cx) / 1000)
            score += max(0, 0.15 - vertical_distance / 1000)
            candidates.append((score, text, element, stamp))

    if not candidates:
        return None

    score, issuer, element, stamp = max(candidates, key=lambda item: item[0])
    result = make_field_result("issuer", issuer, "stamp_nearby", element, score)
    result["stamp_bbox"] = stamp.get("bbox")
    result["stamp_page_number"] = stamp.get("page_number")
    return result


def pick_stamp_nearby_partial_issuer(
    stamp_regions: list[dict[str, Any]],
    elements: list[dict[str, Any]],
    page_sizes: dict[int, tuple[float, float]],
    fallback_issuer: dict[str, Any],
) -> Optional[dict[str, Any]]:
    '''
    用章附近 OCR 残片校准红头或标题前缀中的完整发文单位。

    Args:
        stamp_regions: 红章候选区域列表。
        elements: 已过滤干扰项的文本元素列表。
        page_sizes: PDF 页码到真实宽高的映射。
        fallback_issuer: 红头、标题前缀或落款识别出的完整发文单位。

    Returns:
        如果章附近存在能佐证发文单位的残片，返回带章页坐标的发文单位结果；否则返回 None。
    '''
    issuer_text = normalize_field_text(fallback_issuer.get('text'))
    if not issuer_text:
        return None

    evidence_keywords = issuer_evidence_keywords(issuer_text)
    if not evidence_keywords:
        return None

    candidates = []
    for stamp in stamp_regions:
        page_number = stamp.get('page_number')
        page_size = page_sizes.get(page_number)
        if not page_size:
            continue
        page_height = page_size[1]
        sx0, sy0, sx1, sy1 = stamp['bbox']
        stamp_cx = (sx0 + sx1) / 2
        stamp_center_y = (sy0 + sy1) / 2
        nearby_elements = []
        hit_count = 0

        for element in elements:
            if element.get('page number') != page_number:
                continue
            text = normalize_field_text(element.get('content'))
            if not text or '印发' in text:
                continue
            eb = pdf_bbox_to_top_down_bbox(element['bounding box'], page_height, element.get('type') == 'pymupdf_block')
            ex0, ey0, ex1, ey1 = eb
            ecx = (ex0 + ex1) / 2
            element_center_y = (ey0 + ey1) / 2
            if abs(ecx - stamp_cx) > max(220, (sx1 - sx0) * 2.2):
                continue
            if abs(element_center_y - stamp_center_y) > max(180, (sy1 - sy0) * 1.8):
                continue

            nearby_elements.append(element)
            hit_count += sum(1 for keyword in evidence_keywords if keyword in text)

        if hit_count > 0 and nearby_elements:
            candidates.append((hit_count, stamp, nearby_elements))

    if not candidates:
        return None

    _, stamp, nearby_elements = max(candidates, key=lambda item: item[0])
    result = dict(fallback_issuer)
    result['text'] = issuer_text
    result['source'] = 'stamp_nearby_partial_' + str(fallback_issuer.get('source'))
    result['confidence'] = max(float(result.get('confidence') or 0), 0.72)
    result['page_number'] = stamp.get('page_number')
    result['bbox'] = bbox_union(nearby_elements) or stamp.get('bbox')
    result['source_id'] = [element.get('id') for element in nearby_elements if element.get('id') is not None]
    result['source_type'] = [element.get('type') for element in nearby_elements]
    result['stamp_bbox'] = stamp.get('bbox')
    result['stamp_page_number'] = stamp.get('page_number')
    result['stamp_search_mode'] = 'partial'
    return result


def pick_stamp_nearby_issuer(pdf_path: Path, elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    根据印章位置提取发文单位。

    先搜索常见页范围；若没有找到印章附近发文单位，再全页搜索，避免页数限制漏识别。

    Args:
        pdf_path: PDF 文件路径，用于检测真实红章位置。
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        基于印章位置识别出的发文单位；失败时返回 None。
    """
    if not elements:
        return None

    try:
        page_sizes = get_pdf_page_sizes(pdf_path)
        text_pages = sorted({int(element["page number"]) for element in elements})
        all_pages = sorted(page_sizes)
        quick_pages = sorted(set(text_pages[:2] + text_pages[-3:] + all_pages[:2] + all_pages[-3:]))
        quick_regions = find_red_stamp_regions(pdf_path, quick_pages)
        quick_result = pick_stamp_nearby_issuer_from_regions(quick_regions, elements, page_sizes)
        if quick_result:
            quick_result["stamp_search_mode"] = "quick"
            return quick_result

        remaining_pages = [page for page in all_pages if page not in set(quick_pages)]
        all_regions = quick_regions + find_red_stamp_regions(pdf_path, remaining_pages)
        full_result = pick_stamp_nearby_issuer_from_regions(all_regions, elements, page_sizes)
        if full_result:
            full_result["stamp_search_mode"] = "full"
            return full_result

        stamp_pages = sorted({int(region['page_number']) for region in all_regions})
        native_elements = collect_pymupdf_text_elements(pdf_path, stamp_pages)
        native_result = pick_stamp_nearby_issuer_from_regions(all_regions, native_elements, page_sizes)
        if native_result:
            native_result['source'] = 'stamp_nearby_pymupdf'
            native_result['stamp_search_mode'] = 'pymupdf'
            return native_result
        return None
    except Exception as exc:
        print(f'印章发文单位提取失败: {exc}')
        return None


def find_best_stamp_region(pdf_path: Path) -> Optional[dict[str, Any]]:
    '''
    查找 PDF 中面积最大的真实红章区域。

    Args:
        pdf_path: PDF 文件路径。

    Returns:
        面积最大的红章区域；没有检测到红章或检测失败时返回 None。
    '''
    try:
        page_numbers = sorted(get_pdf_page_sizes(pdf_path))
        regions = find_red_stamp_regions(pdf_path, page_numbers)
    except Exception as exc:
        print(f'红章区域检测失败: {exc}')
        return None

    if not regions:
        return None
    return max(regions, key=lambda region: region.get('area', 0))


def pick_red_header_issuer(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    从红头区域抽取发文单位。

    有些 PDF 带大面积水印，会干扰页面范围估算，所以这里不只依赖顶部坐标。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        红头文件行中的发文单位；没有匹配时返回 None。
    """
    if not elements:
        return None

    first_page = min(int(element["page number"]) for element in elements)
    extent = page_extent(elements, first_page)
    candidates = []
    for element in elements:
        if element.get("page number") != first_page:
            continue

        text = normalize_field_text(element["content"])
        if "文件" not in text or not looks_like_issuer(text):
            continue

        issuer = text.replace("文件", "").strip()
        score = 0.55
        if element.get("type") == "heading":
            score += 0.2
        if extent:
            x_ratio, y_ratio = normalized_center(element["bounding box"], extent)
            score += max(0, 0.15 - abs(x_ratio - 0.5))
            if y_ratio <= 0.45:
                score += 0.1
        candidates.append((score, issuer, element))

    if not candidates:
        return None

    score, issuer, element = max(candidates, key=lambda item: item[0])
    return make_field_result("issuer", issuer, "red_header", element, score)


def pick_top_header_issuer(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    '''
    从首页顶部抬头中抽取发文单位。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        首页顶部的单位抬头；没有匹配时返回 None。
    '''
    if not elements:
        return None

    first_page = min(int(element['page number']) for element in elements)
    candidates = []
    for element in top_page_elements(elements, first_page, max_y_ratio=0.28):
        text = normalize_field_text(element['content'])
        if any(keyword in text for keyword in TITLE_KEYWORDS):
            continue
        if not looks_like_issuer(text):
            continue

        score = 0.68
        if element.get('type') == 'heading':
            score += 0.16
        if len(text) >= 10:
            score += 0.08
        candidates.append((score, text, element))

    if not candidates:
        return None

    score, issuer, element = max(candidates, key=lambda item: item[0])
    return make_field_result('issuer', issuer, 'top_header', element, score)


def split_issuer_from_title_text(text: str) -> Optional[str]:
    """
    从标题前缀中拆出发文单位。

    Args:
        text: 可能包含发文单位前缀的标题文本。

    Returns:
        发文单位前缀；不能确认时返回 None。
    """
    text = normalize_field_text(text)
    split_keywords = ("关于", "转发", "印发", "批转", "发布", "公布", "同意")
    positions = [text.find(keyword) for keyword in split_keywords if text.find(keyword) > 0]
    if not positions:
        return None

    prefix = text[:min(positions)].strip()
    return prefix if looks_like_issuer(prefix) else None


def pick_title_prefix_issuer(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    从标题前缀中抽取发文单位。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        标题前缀中的发文单位；没有匹配时返回 None。
    """
    if not elements:
        return None

    first_page = min(int(element["page number"]) for element in elements)
    candidates = []
    for raw_title, element in heading_title_groups(elements, first_page):
        issuer = split_issuer_from_title_text(raw_title)
        if issuer:
            score = 0.65
            if element.get("type") == "heading":
                score += 0.1
            candidates.append((score, issuer, element))

    if not candidates:
        return None

    score, issuer, element = max(candidates, key=lambda item: item[0])
    return make_field_result("issuer", issuer, "title_prefix", element, score)


def pick_document_number(elements: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    抽取公文文号。

    Args:
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        文号字段结果；未找到时返回 None。
    """
    if not elements:
        return None

    first_page = min(int(element["page number"]) for element in elements)
    candidates = []
    for element in elements:
        if element.get("page number") != first_page:
            continue
        text = normalize_field_text(element["content"])
        if "号" not in text or not any(char.isdigit() for char in text):
            continue
        if len(text) > 45:
            continue

        score = 0.5
        if "〔" in text and "〕" in text:
            score += 0.25
        if re.search(r"\d{4}", text):
            score += 0.15
        if element.get("type") == "paragraph":
            score += 0.05
        candidates.append((score, text, element))

    if not candidates:
        return None

    score, text, element = max(candidates, key=lambda item: item[0])
    return make_field_result("document_number", text, "document_number_line", element, score)


def heading_title_groups(elements: list[dict[str, Any]], first_page: int) -> list[tuple[str, dict[str, Any]]]:
    """
    生成标题候选，包含单个 heading 和相邻 heading 合并后的候选。

    Args:
        elements: 已过滤干扰项的文本元素列表。
        first_page: 首页页码。

    Returns:
        标题文本和来源元素组成的候选列表。
    """
    top_elements = top_page_elements(elements, first_page, max_y_ratio=0.55)
    heading_elements = [element for element in top_elements if element.get("type") == "heading"]
    groups: list[tuple[str, dict[str, Any]]] = []

    for element in heading_elements:
        groups.append((element["content"], element))

    for index in range(len(heading_elements) - 1):
        first = heading_elements[index]
        second = heading_elements[index + 1]
        if abs(first.get("_y_ratio", 0) - second.get("_y_ratio", 0)) <= 0.18:
            merged = dict(second)
            merged["content"] = f"{first['content']} {second['content']}"
            merged["bounding box"] = bbox_union([first, second]) or second["bounding box"]
            groups.append((merged["content"], merged))

    return groups


def pick_document_title(elements: list[dict[str, Any]], issuer: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    从候选中抽取正文标题，并尽量去掉发文单位前缀。

    Args:
        elements: 已过滤干扰项的文本元素列表。
        issuer: 已识别的发文单位，用于清理标题前缀。

    Returns:
        标题字段结果；无法判断时返回 None。
    """
    if not elements:
        return None

    first_page = min(int(element["page number"]) for element in elements)
    extent = page_extent(elements, first_page)
    if extent is None:
        return None

    candidates = []
    for raw_title, element in heading_title_groups(elements, first_page):
        text = normalize_field_text(raw_title)
        if text.endswith("文件") and looks_like_issuer(text):
            continue

        clean_title = strip_issuer_from_title(text, issuer.get("text") if issuer else None)
        if len(clean_title) < 8:
            continue

        x_ratio, y_ratio = normalized_center(element["bounding box"], extent)
        score = 0.35
        score += max(0, 0.2 - abs(x_ratio - 0.5))
        score += 0.2 if any(keyword in clean_title for keyword in TITLE_KEYWORDS) else 0
        score += 0.15 if "关于" in clean_title else 0
        score += 0.1 if len(clean_title) >= 14 else 0
        score += max(0, 0.12 - abs(y_ratio - 0.30))
        candidates.append((score, clean_title, element))

    # 如果解析器没有给 heading，就用第一页上半区的长文本兜底。
    if not candidates:
        for element in top_page_elements(elements, first_page, max_y_ratio=0.55):
            text = normalize_field_text(element["content"])
            clean_title = strip_issuer_from_title(text, issuer.get("text") if issuer else None)
            if len(clean_title) >= 14 and any(keyword in clean_title for keyword in TITLE_KEYWORDS):
                candidates.append((0.45, clean_title, element))

    if not candidates:
        return None

    score, title, element = max(candidates, key=lambda item: item[0])
    return make_field_result("title", title, "title_candidate", element, score)


def pick_signature_issuer(signature: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    从落款中兜底抽取发文单位。

    Args:
        signature: 落款区域结果，可为空。

    Returns:
        落款中的发文单位；没有匹配时返回 None。
    """
    if not signature:
        return None

    lines = [normalize_field_text(line) for line in signature.get("text", "").splitlines()]
    for line in lines:
        line = re.sub(r"\d{4}.*", "", line).strip()
        if looks_like_issuer(line):
            return make_field_result("issuer", line, "signature", {"page number": signature.get("page_number"), "bounding box": signature.get("bbox")}, 0.55)
    return None


def cell_text(cell: dict[str, Any]) -> str:
    """
    提取表格单元格文本。

    Args:
        cell: opendataloader 表格单元格节点。

    Returns:
        单元格内合并后的文本，已排除水印等干扰项。
    """
    parts = []
    for element in iter_elements(cell):
        if element is cell:
            continue
        content = clean_text(element.get("content"))
        if content and not is_noise_text(content):
            parts.append(content)
    return " ".join(parts).strip()


def table_to_rows(table: dict[str, Any]) -> list[list[str]]:
    """
    将 opendataloader 的 table 结构转换为二维文本数组。

    Args:
        table: opendataloader 表格节点。

    Returns:
        表格二维行列文本，空行会被过滤。
    """
    rows = []
    for row in table.get("rows", []):
        cells = row.get("cells", [])
        row_text = [cell_text(cell) for cell in cells]
        if any(row_text):
            rows.append(row_text)
    return rows


def table_text(rows: list[list[str]]) -> str:
    """
    将二维表格文本合并为便于检索的字符串。

    Args:
        rows: 表格二维行列文本。

    Returns:
        用换行和竖线拼接后的检索文本。
    """
    return "\n".join(" | ".join(cell for cell in row if cell) for row in rows)


def is_scoring_table(rows: list[list[str]], nearby_text: str = "") -> bool:
    """
    判断是否为评分表。

    Args:
        rows: 表格二维行列文本。
        nearby_text: 表格附近的标题或说明文本。

    Returns:
        符合评分表关键词特征时返回 True。
    """
    text = table_text(rows) + "\n" + nearby_text
    keywords = ("评分", "分值", "得分", "自评", "评分标准", "指标", "竞争配置")
    strong_hits = sum(1 for keyword in keywords if keyword in text)
    if strong_hits >= 2:
        return True

    header = " ".join(rows[0]) if rows else ""
    return "评分标准" in header or ("得分" in header and "项目" in header)


def nearby_text_for_table(elements: list[dict[str, Any]], table: dict[str, Any]) -> str:
    """
    获取表格同页附近文本，用于识别表名。

    Args:
        elements: 已过滤干扰项的文本元素列表。
        table: opendataloader 表格节点。

    Returns:
        表格附近文本，已排除水印等干扰项。
    """
    table_bbox = get_bbox(table)
    page_number = table.get("page number")
    if not table_bbox or page_number is None:
        return ""

    pieces = []
    table_top = table_bbox[3]
    for element in elements:
        if element.get("page number") != page_number:
            continue
        bbox = element.get("bounding box")
        if not bbox:
            continue
        distance = abs(bbox[3] - table_top)
        if distance <= 120 and not is_noise_text(element['content']):
            pieces.append(element["content"])
    return " ".join(pieces)


def extract_scoring_tables(doc: dict[str, Any], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    抽取评分表。

    优先使用结构化 table；如果没有 table，则用关键词段落做弱兜底。

    Args:
        doc: opendataloader 输出的完整 JSON。
        elements: 已过滤干扰项的文本元素列表。

    Returns:
        竞争性配置评分表列表，保持 rows/header/nearby_text/source 结构。
    """
    result = []
    for table_index, table in enumerate((e for e in iter_elements(doc) if e.get("type") == "table"), 1):
        rows = table_to_rows(table)
        nearby_text = nearby_text_for_table(elements, table)
        if not rows or not is_scoring_table(rows, nearby_text):
            continue

        result.append(
            {
                "name": "scoring_table",
                "index": len(result) + 1,
                "page_number": table.get("page number"),
                "bbox": get_bbox(table),
                "rows": rows,
                "header": rows[0] if rows else [],
                "nearby_text": nearby_text,
                "source": "table",
            }
        )

    if result:
        return result

    fallback_keywords = ("评分", "分值", "得分", "自评", "评分标准", "竞争配置")
    fallback = []
    for element in elements:
        text = element["content"]
        if is_noise_text(text):
            continue
        if any(keyword in text for keyword in fallback_keywords):
            fallback.append(element)

    if fallback:
        result.append(
            {
                "name": "scoring_table",
                "index": 1,
                "page_number": fallback[0].get("page number"),
                "bbox": bbox_union(fallback),
                "rows": [[element["content"]] for element in fallback],
                "header": [],
                "nearby_text": "",
                "source": "paragraph_fallback",
            }
        )

    return result


def extract_key_blocks(doc: dict[str, Any], pdf_path: Optional[Path] = None, allow_issuer_fallback: bool = True) -> dict[str, Any]:
    """
    从 PDF 解析结果中抽取关键区域和关键字段。

    Args:
        doc: opendataloader 输出的完整 JSON。
        pdf_path: PDF 文件路径；提供后才会启用红章检测。
        allow_issuer_fallback: 是否允许在章附近文字未命中时使用红头、标题前缀或落款兜底。

    Returns:
        包含 title、issuer_from_stamp、competitive_scoring_tables 三个字段的结果字典。
    """
    elements = collect_text_elements(doc)
    signature = pick_signature(elements)
    stamp_issuer = pick_stamp_nearby_issuer(pdf_path, elements) if pdf_path else None
    fallback_issuer = pick_red_header_issuer(elements) or pick_top_header_issuer(elements) or pick_title_prefix_issuer(elements) or pick_signature_issuer(signature)
    issuer_from_stamp = stamp_issuer
    if issuer_from_stamp and fallback_issuer:
        stamp_text = normalize_field_text(issuer_from_stamp.get('text'))
        fallback_text = normalize_field_text(fallback_issuer.get('text'))
        if stamp_text and fallback_text and stamp_text != fallback_text and stamp_text in fallback_text:
            completed = dict(fallback_issuer)
            completed['text'] = fallback_text
            completed['source'] = 'stamp_nearby_completed_' + str(fallback_issuer.get('source'))
            completed['confidence'] = max(float(issuer_from_stamp.get('confidence') or 0), float(completed.get('confidence') or 0))
            completed['page_number'] = issuer_from_stamp.get('page_number')
            completed['bbox'] = issuer_from_stamp.get('bbox')
            completed['source_id'] = issuer_from_stamp.get('source_id')
            completed['source_type'] = issuer_from_stamp.get('source_type')
            completed['stamp_bbox'] = issuer_from_stamp.get('stamp_bbox')
            completed['stamp_page_number'] = issuer_from_stamp.get('stamp_page_number')
            completed['stamp_search_mode'] = issuer_from_stamp.get('stamp_search_mode')
            issuer_from_stamp = completed
    if allow_issuer_fallback and issuer_from_stamp is None and fallback_issuer:
        stamp_region = find_best_stamp_region(pdf_path) if pdf_path else None
        partial_issuer = None
        if pdf_path and stamp_region:
            page_sizes = get_pdf_page_sizes(pdf_path)
            partial_issuer = pick_stamp_nearby_partial_issuer([stamp_region], elements, page_sizes, fallback_issuer)
        if partial_issuer:
            issuer_from_stamp = partial_issuer
        else:
            issuer_from_stamp = dict(fallback_issuer)
            issuer_from_stamp['source'] = 'stamp_fallback_' + str(fallback_issuer.get('source')) if stamp_region else fallback_issuer.get('source')
            if stamp_region:
                issuer_from_stamp['stamp_bbox'] = stamp_region.get('bbox')
                issuer_from_stamp['stamp_page_number'] = stamp_region.get('page_number')
                issuer_from_stamp['stamp_search_mode'] = 'fallback'
    title_issuer_hint = issuer_from_stamp or fallback_issuer
    title_field = pick_document_title(elements, title_issuer_hint)

    title_block = pick_title(elements)
    if title_block and title_field:
        title_block["text"] = title_field["text"]

    if title_block is None and title_field:
        title_block = title_field

    scoring_tables = extract_scoring_tables(doc, elements)

    return {
        "title": title_block,
        "issuer_from_stamp": issuer_from_stamp,
        "competitive_scoring_tables": scoring_tables,
    }


def save_key_blocks(key_blocks: dict[str, Any], output_dir: Path, pdf_path: Path) -> Path:
    """
    保存关键区域抽取结果。

    Args:
        key_blocks: 关键字段抽取结果。
        output_dir: 当前 PDF 输出目录。
        pdf_path: 原始 PDF 文件路径。

    Returns:
        写入的 key_blocks JSON 文件路径。
    """
    output_path = output_dir / f"{pdf_path.stem}_key_blocks.json"
    output_path.write_text(json.dumps(key_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def print_summary(doc: dict[str, Any], output_dir: Path, preview_limit: int) -> None:
    """
    打印 PDF 解析结果的摘要信息和文本预览。

    Args:
        doc: opendataloader 输出的完整 JSON。
        output_dir: 当前 PDF 输出目录。
        preview_limit: 最多打印的文本预览条数。

    Returns:
        无返回值，直接打印摘要。
    """
    elements = list(iter_elements(doc))
    counts = Counter(str(element.get("type", "unknown")) for element in elements)

    print("\n解析摘要")
    print("-" * 60)
    print(f"文件: {doc.get('file name')}")
    print(f"页数: {doc.get('number of pages')}")
    print(f"元素总数: {len(elements)}")
    print(f"元素类型分布: {dict(counts)}")
    print(f"输出目录: {output_dir.resolve()}")

    print("\n文本预览")
    print("-" * 60)
    shown = 0
    for element in elements:
        content = clean_text(element.get("content"))
        if not content:
            continue
        page = element.get("page number", "?")
        element_type = element.get("type", "unknown")
        print(f"[{shown + 1}] 页码={page} 类型={element_type}: {content[:160]}")
        shown += 1
        if shown >= preview_limit:
            break
    if shown == 0:
        print("JSON 输出中未找到文本内容。")


def has_text_content(doc: dict[str, Any]) -> bool:
    """
    判断解析结果中是否存在有效文本元素。

    Args:
        doc: opendataloader 输出的完整 JSON。

    Returns:
        存在非空文本时返回 True。
    """
    return any(not is_noise_text(element.get("content")) for element in iter_elements(doc))


def run_opendataloader_convert(
    pdf_path: Path,
    output_dir: Path,
    formats: list[str],
    pages: Optional[str],
    verbose: bool,
    hybrid_backend: Optional[str] = None,
    hybrid_url: Optional[str] = None,
    hybrid_mode: str = "full",
) -> None:
    """
    调用 opendataloader-pdf 执行解析。

    hybrid_backend 为空时走普通解析；不为空时走本地 hybrid OCR 服务。

    Args:
        pdf_path: PDF 文件路径。
        output_dir: 输出目录。
        formats: 输出格式列表。
        pages: 可选页码范围。
        verbose: 是否显示详细日志。
        hybrid_backend: hybrid 后端名称，为空时不启用。
        hybrid_url: hybrid 服务地址。
        hybrid_mode: hybrid 路由模式。

    Returns:
        无返回值，解析结果由 opendataloader 写入输出目录。
    """
    options = {
        "input_path": str(pdf_path),
        "output_dir": str(output_dir),
        "format": ",".join(formats),
        "pages": pages,
        "reading_order": "xycut",
        "quiet": not verbose,
    }

    if hybrid_backend:
        options.update(
            {
                "hybrid": hybrid_backend,
                "hybrid_mode": hybrid_mode,
                "hybrid_url": hybrid_url,
                "hybrid_timeout": "120",
                "hybrid_fallback": True,
            }
        )

    opendataloader_pdf.convert(**options)


def page_range_to_numbers(pages: Optional[str], total_pages: int) -> list[int]:
    """
    将页码范围字符串转换为页码列表。

    支持格式：None、"1"、"1-3"、"1,3-5"。

    Args:
        pages: 页码范围字符串。
        total_pages: PDF 总页数。

    Returns:
        去重并排序后的页码列表。
    """
    if not pages:
        return list(range(1, total_pages + 1))

    result: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = max(1, int(start_text.strip()))
            end = min(total_pages, int(end_text.strip()))
            result.extend(range(start, end + 1))
        else:
            page_number = int(part)
            if 1 <= page_number <= total_pages:
                result.append(page_number)

    return sorted(set(result))


def page_numbers_to_ranges(page_numbers: list[int], batch_size: int) -> list[str]:
    """
    将页码列表按批次转换为 opendataloader 支持的页码范围字符串。

    Args:
        page_numbers: 页码列表。
        batch_size: 每批最大页数。

    Returns:
        页码范围字符串列表。
    """
    if batch_size <= 0:
        raise ValueError("hybrid_batch_size 必须大于 0")

    ranges = []
    for index in range(0, len(page_numbers), batch_size):
        batch = page_numbers[index:index + batch_size]
        if not batch:
            continue
        ranges.append(str(batch[0]) if batch[0] == batch[-1] else f"{batch[0]}-{batch[-1]}")
    return ranges


def renumber_element_ids(node: Any, next_id: int) -> int:
    """
    递归重排元素 id，避免多个分页 JSON 合并后 id 重复。

    Args:
        node: JSON 根节点、子节点或节点列表。
        next_id: 下一个可用 id。

    Returns:
        重排后下一个可用 id。
    """
    if isinstance(node, dict):
        if "id" in node:
            node["id"] = next_id
            next_id += 1
        for key in CHILD_KEYS:
            child = node.get(key)
            if child:
                next_id = renumber_element_ids(child, next_id)
    elif isinstance(node, list):
        for item in node:
            next_id = renumber_element_ids(item, next_id)
    return next_id


def merge_text_file(batch_dirs: list[Path], final_path: Path, suffix: str) -> None:
    """
    合并分页解析产生的 markdown 或 text 文件。

    Args:
        batch_dirs: 分页输出目录列表。
        final_path: 合并后的目标文件路径。
        suffix: 需要合并的文件后缀。

    Returns:
        无返回值，结果写入 final_path。
    """
    chunks = []
    for batch_dir in batch_dirs:
        files = sorted(batch_dir.glob(f"*{suffix}"))
        if not files:
            continue
        content = files[0].read_text(encoding="utf-8").strip()
        if content:
            chunks.append(content)

    final_path.write_text("\n\n".join(chunks), encoding="utf-8")


def merge_batch_outputs(
    base_doc: dict[str, Any],
    batch_dirs: list[Path],
    output_dir: Path,
    pdf_path: Path,
    formats: list[str],
) -> dict[str, Any]:
    """
    合并分页 hybrid 输出，生成与 opendataloader 原始结构兼容的 JSON。

    Args:
        base_doc: 普通解析产生的基础 JSON。
        batch_dirs: hybrid 分页输出目录列表。
        output_dir: 当前 PDF 正式输出目录。
        pdf_path: 原始 PDF 文件路径。
        formats: 需要合并的输出格式。

    Returns:
        合并后的 JSON 字典。
    """
    merged_doc = dict(base_doc)
    merged_doc["kids"] = []
    next_id = 1

    for batch_dir in batch_dirs:
        batch_doc = load_json_result(batch_dir, pdf_path)
        batch_kids = batch_doc.get("kids", [])
        next_id = renumber_element_ids(batch_kids, next_id)
        merged_doc["kids"].extend(batch_kids)

    json_path = output_dir / f"{pdf_path.stem}.json"
    json_path.write_text(json.dumps(merged_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if "markdown" in formats:
        merge_text_file(batch_dirs, output_dir / f"{pdf_path.stem}.md", ".md")
    if "text" in formats:
        merge_text_file(batch_dirs, output_dir / f"{pdf_path.stem}.txt", ".txt")

    return merged_doc


def run_hybrid_convert_in_batches(
    pdf_path: Path,
    output_dir: Path,
    base_doc: dict[str, Any],
    formats: list[str],
    pages: Optional[str],
    verbose: bool,
    hybrid_backend: str,
    hybrid_url: str,
    hybrid_mode: str,
    hybrid_batch_size: int,
) -> dict[str, Any]:
    """
    分页调用 hybrid OCR，并把每批结果合并到正式输出目录。

    Args:
        pdf_path: PDF 文件路径。
        output_dir: 当前 PDF 输出目录。
        base_doc: 普通解析产生的基础 JSON。
        formats: 输出格式列表。
        pages: 可选页码范围。
        verbose: 是否显示详细日志。
        hybrid_backend: hybrid 后端名称。
        hybrid_url: hybrid 服务地址。
        hybrid_mode: hybrid 路由模式。
        hybrid_batch_size: 每批 OCR 页数。

    Returns:
        合并后的 JSON 字典。
    """
    total_pages = int(base_doc.get("number of pages") or 0)
    if total_pages <= 0:
        raise ValueError("解析结果中没有有效页数，无法分页 hybrid OCR")

    page_numbers = page_range_to_numbers(pages, total_pages)
    page_ranges = page_numbers_to_ranges(page_numbers, hybrid_batch_size)
    batch_root = output_dir / "_hybrid_batches"
    batch_root.mkdir(parents=True, exist_ok=True)

    batch_dirs = []
    print(f"hybrid 分页批次: {', '.join(page_ranges)}")
    for batch_index, page_range in enumerate(page_ranges, 1):
        batch_dir = batch_root / f"batch_{batch_index:03d}_{page_range.replace('-', '_')}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{batch_index}/{len(page_ranges)}] hybrid OCR 页码: {page_range}")

        try:
            run_opendataloader_convert(
                pdf_path=pdf_path,
                output_dir=batch_dir,
                formats=formats,
                pages=page_range,
                verbose=verbose,
                hybrid_backend=hybrid_backend,
                hybrid_url=hybrid_url,
                hybrid_mode=hybrid_mode,
            )
        except Exception as exc:
            print(f"hybrid OCR 批次失败，页码 {page_range}: {exc}")
            continue

        batch_dirs.append(batch_dir)

    if not batch_dirs:
        raise RuntimeError("所有 hybrid OCR 批次均失败，无法生成合并结果")

    return merge_batch_outputs(base_doc, batch_dirs, output_dir, pdf_path, formats)


def pick_stamp_issuer_with_hybrid(
    pdf_path: Path,
    output_dir: Path,
    stamp_region: dict[str, Any],
    verbose: bool,
    hybrid_backend: str,
    hybrid_url: str,
    hybrid_mode: str,
) -> Optional[dict[str, Any]]:
    '''
    只对红章所在页执行 hybrid OCR，并按同一个红章坐标重新提取发文单位。

    Args:
        pdf_path: PDF 文件路径。
        output_dir: 当前 PDF 输出目录。
        stamp_region: 已检测到的红章区域。
        verbose: 是否显示 opendataloader 详细日志。
        hybrid_backend: hybrid 后端名称。
        hybrid_url: hybrid 服务地址。
        hybrid_mode: hybrid 路由模式。

    Returns:
        hybrid OCR 后识别到的章附近发文单位；失败或未识别时返回 None。
    '''
    page_number = int(stamp_region.get('page_number') or 0)
    if page_number <= 0:
        return None

    stamp_dir = output_dir / '_hybrid_stamp_pages' / f'page_{page_number}'
    stamp_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_opendataloader_convert(
            pdf_path=pdf_path,
            output_dir=stamp_dir,
            formats=['json'],
            pages=str(page_number),
            verbose=verbose,
            hybrid_backend=hybrid_backend,
            hybrid_url=hybrid_url,
            hybrid_mode=hybrid_mode,
        )
        hybrid_doc = load_json_result(stamp_dir, pdf_path)
        hybrid_elements = collect_text_elements(hybrid_doc)
        page_sizes = get_pdf_page_sizes(pdf_path)
        stamp_regions = find_red_stamp_regions(pdf_path, [page_number]) or [stamp_region]
        result = pick_stamp_nearby_issuer_from_regions(stamp_regions, hybrid_elements, page_sizes)
        if result:
            result['source'] = 'stamp_nearby_hybrid'
            result['stamp_search_mode'] = 'hybrid'
        return result
    except Exception as exc:
        print(f'hybrid 印章页发文单位提取失败，页码 {page_number}: {exc}')
        return None


def print_key_blocks(key_blocks: dict[str, Any], output_path: Path) -> None:
    """
    打印最终抽取结果。

    Args:
        key_blocks: 关键字段抽取结果。
        output_path: key_blocks JSON 文件路径。

    Returns:
        无返回值，直接打印结果摘要。
    """
    print("\n最终抽取结果")
    print("-" * 60)

    title = key_blocks.get("title")
    if title:
        print(f"标题: {title.get('text')}")
        print(f"标题页码: {title.get('page_number')}")
    else:
        print("标题: 未识别")

    issuer = key_blocks.get("issuer_from_stamp")
    if issuer:
        print(f"根据印章获取的发文单位: {issuer.get('text')}")
        print(f"发文单位页码: {issuer.get('page_number')}")
        print(f"印章页码: {issuer.get('stamp_page_number')}")
        print(f"印章坐标: {issuer.get('stamp_bbox')}")
    else:
        print("根据印章获取的发文单位: 未识别")

    scoring_tables = key_blocks.get("competitive_scoring_tables") or []
    print(f"竞争性配置评分表: {len(scoring_tables)} 个")
    for table in scoring_tables:
        print(f"评分表 {table.get('index')}: 第 {table.get('page_number')} 页，{len(table.get('rows') or [])} 行，来源={table.get('source')}")

    print(f"结果文件: {output_path}")


def parse_pdf(
    pdf_path: Union[str, Path],
    output_dir: Union[str, Path] = "output",
    pages: Optional[str] = None,
    formats: Optional[List[str]] = None,
    preview_limit: int = 5,
    verbose: bool = False,
    extract_blocks: bool = True,
    hybrid_on_empty: bool = True,
    hybrid_backend: str = "docling-fast",
    hybrid_url: str = "http://127.0.0.1:5003",
    hybrid_mode: str = "full",
    hybrid_batch_size: int = 5,
) -> dict[str, Any]:
    """
    解析 PDF 文件，并可选抽取标题和落款区域。

    Args:
        pdf_path: PDF 文件路径。
        output_dir: 输出根目录，程序会在该目录下按 PDF 文件名创建子文件夹。
        pages: 可选页码范围，例如 "1" 或 "1,3-5"，为空表示解析全部页面。
        formats: 输出格式列表，默认输出 json、markdown、text。
        preview_limit: 文本预览条数。
        verbose: 是否显示 opendataloader-pdf 的详细日志。
        extract_blocks: 是否抽取标题和落款区域。
        hybrid_on_empty: 普通解析没有识别到文本时，是否自动改用 hybrid OCR 解析。
        hybrid_backend: hybrid 后端名称，当前推荐 docling-fast。
        hybrid_url: 本地 hybrid 服务地址。
        hybrid_mode: hybrid 路由模式，扫描件建议使用 full。
        hybrid_batch_size: hybrid OCR 每批处理的页数，用于降低扫描件内存压力。

    Returns:
        PDF 解析结果；如果 extract_blocks=True，会额外包含 key_blocks 字段。
    """
    if formats is None:
        formats = ["json", "markdown", "text"]

    pdf_path = Path(pdf_path).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"输入文件必须是 PDF 格式: {pdf_path}")

    output_dir = output_root / safe_folder_name(pdf_path.stem)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"输入 PDF: {pdf_path}")
    print(f"输出根目录: {output_root}")
    print(f"当前文件输出目录: {output_dir}")
    print(f"输出格式: {', '.join(formats)}")
    if pages:
        print(f"页码范围: {pages}")

    run_opendataloader_convert(
        pdf_path=pdf_path,
        output_dir=output_dir,
        formats=formats,
        pages=pages,
        verbose=verbose,
    )

    doc = load_json_result(output_dir, pdf_path)
    if hybrid_on_empty and not has_text_content(doc):
        print("\n普通解析未识别到文本，开始使用 hybrid OCR 重新解析。")
        print(f"hybrid 后端: {hybrid_backend}")
        print(f"hybrid 服务: {hybrid_url}")
        doc = run_hybrid_convert_in_batches(
            pdf_path=pdf_path,
            output_dir=output_dir,
            base_doc=doc,
            formats=formats,
            pages=pages,
            verbose=verbose,
            hybrid_backend=hybrid_backend,
            hybrid_url=hybrid_url,
            hybrid_mode=hybrid_mode,
            hybrid_batch_size=hybrid_batch_size,
        )

    print_summary(doc, output_dir, max(preview_limit, 0))

    if extract_blocks:
        key_blocks = extract_key_blocks(doc, pdf_path, allow_issuer_fallback=False)
        if not key_blocks.get("issuer_from_stamp") and hybrid_on_empty:
            stamp_region = find_best_stamp_region(pdf_path)
            if stamp_region:
                print(f"检测到红章但普通解析未提取到发文单位，开始 hybrid OCR 印章页: {stamp_region.get('page_number')}")
                hybrid_issuer = pick_stamp_issuer_with_hybrid(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    stamp_region=stamp_region,
                    verbose=verbose,
                    hybrid_backend=hybrid_backend,
                    hybrid_url=hybrid_url,
                    hybrid_mode=hybrid_mode,
                )
                if hybrid_issuer:
                    key_blocks["issuer_from_stamp"] = hybrid_issuer

        if not key_blocks.get("issuer_from_stamp"):
            key_blocks = extract_key_blocks(doc, pdf_path, allow_issuer_fallback=True)

        doc["key_blocks"] = key_blocks
        key_blocks_path = save_key_blocks(key_blocks, output_dir, pdf_path)
        print_key_blocks(key_blocks, key_blocks_path)

    return doc


if __name__ == "__main__":
    # 示例：解析指定 PDF 文件，并抽取标题和落款区域。

    # result = parse_pdf(
    #     pdf_path=r"C:\path\to\your.pdf",
    #     output_dir="output",
    #     pages=None,
    #     formats=["json", "markdown", "text"],
    #     preview_limit=5,
    #     verbose=False,
    #     extract_blocks=True,
    # )
    #
    # print("\n处理完成。")
    folder_path = r"C:\Users\EDY\Desktop\PDF\支持性文件\环评"  # 修改为你的文件夹路径

    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"文件夹不存在: {folder}")

    # 获取文件夹下所有PDF文件（包括子文件夹）
    pdf_files = sorted(folder.rglob("*.pdf"))

    if not pdf_files:
        print(f"在 {folder} 中未找到PDF文件")
    else:
        print(f"找到 {len(pdf_files)} 个PDF文件\n")

        for index, pdf_file in enumerate(pdf_files, 1):
            print(f"\n{'=' * 60}")
            print(f"[{index}/{len(pdf_files)}] 正在处理: {pdf_file.name}")
            print(f"{'=' * 60}")

            try:
                # 为每个PDF创建独立的输出子目录

                result = parse_pdf(
                    pdf_path=pdf_file,
                    output_dir="output",
                    pages=None,  # 解析所有页面，或指定页码如 "1-3"
                    formats=["json", "markdown", "text"],
                    preview_limit=5,
                    verbose=False,
                )

                print(f"✓ {pdf_file.name} 处理完成")

            except Exception as e:
                print(f"✗ {pdf_file.name} 处理失败: {str(e)}")
                continue

        print(f"\n{'=' * 60}")
        print(f"全部处理完成！共处理 {len(pdf_files)} 个PDF文件")
        print(f"输出目录: output/")
        print(f"{'=' * 60}")
