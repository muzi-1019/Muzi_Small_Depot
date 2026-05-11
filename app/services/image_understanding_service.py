"""
本文件的作用：用户聊天图片理解服务。
支持两类图片：
1. 有文字的图片：优先用 OCR 提取文字，例如截图、票据、表格、公告图片；
2. 无明显文字的图片：调用视觉大模型生成图片描述，例如照片、图表、示意图。

为什么采用 OCR + 视觉模型双通道：
- OCR 对图片中的明确文字更稳定，能保留金额、日期、公司名等精确信息；
- 视觉模型能理解没有文字的场景、图表趋势、物体关系；
- 两者合并后作为多模态上下文交给 LLM，既能回答“图上写了什么”，也能回答“图里是什么”。
"""

import base64
import io
import logging
import re

import httpx
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)


class ImageUnderstandingService:
    """图片理解服务：把用户上传的图片转换成可被大模型使用的中文文本上下文"""

    @staticmethod
    def analyze(image_data: str | None, image_mime: str | None = None) -> str:
        """解析用户上传图片，返回 OCR 文本和视觉描述组成的多模态上下文"""
        image_bytes, mime = ImageUnderstandingService._decode_image(image_data, image_mime)
        if not image_bytes:
            if image_data:
                logger.warning("图片解析跳过：图片数据解码为空 mime=%s data_len=%d", image_mime, len(image_data))
            return ""
        logger.info("开始图片解析 mime=%s bytes=%d", mime, len(image_bytes))
        ocr_text = ImageUnderstandingService._ocr_image(image_bytes)
        vision_desc = ImageUnderstandingService._describe_image(image_bytes, mime)
        parts: list[str] = []
        if ocr_text:
            parts.append(f"【图片OCR文字】\n{ocr_text}")
        if vision_desc:
            parts.append(f"【图片视觉描述】\n{vision_desc}")
        if not parts:
            parts.append("【图片解析结果】已收到图片，但未识别到明确文字或可描述内容。")
        logger.info("图片解析完成 mime=%s ocr_len=%d vision_len=%d context_len=%d", mime, len(ocr_text), len(vision_desc), len("\n\n".join(parts)))
        return "\n\n".join(parts)

    @staticmethod
    def _decode_image(image_data: str | None, image_mime: str | None = None) -> tuple[bytes, str]:
        """解码前端传来的 dataURL/base64 图片，并返回图片字节和 MIME 类型"""
        if not image_data:
            return b"", image_mime or "image/png"
        data = image_data.strip()
        mime = image_mime or "image/png"
        if data.startswith("data:"):
            header, _, payload = data.partition(",")
            match = re.match(r"data:([^;]+);base64", header)
            if match:
                mime = match.group(1)
            data = payload
        try:
            raw = base64.b64decode(data, validate=False)
        except Exception as exc:
            logger.warning("图片 base64 解码失败 mime=%s data_len=%d error=%s", mime, len(data), exc)
            return b"", mime
        return raw, mime

    @staticmethod
    def _ocr_image(image_bytes: bytes) -> str:
        """使用 RapidOCR 识别图片文字；未安装 OCR 依赖时自动跳过"""
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            logger.warning("图片 OCR 跳过：未安装 rapidocr_onnxruntime error=%s", exc)
            return ""
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            result, _ = RapidOCR()(image)
            if not result:
                return ""
            lines = [str(line[1]).strip() for line in result if len(line) > 1 and str(line[1]).strip()]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("图片 OCR 失败: %s", exc, exc_info=True)
            return ""

    @staticmethod
    def _describe_image(image_bytes: bytes, mime: str = "image/png") -> str:
        """调用 OpenAI 兼容视觉模型描述图片内容，适合无文字图片、图表、照片"""
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if not base_url or not api_key:
            logger.warning("图片视觉描述跳过：openai_api_base 或 openai_api_key 未配置")
            return ""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": settings.vision_model_name,
            "messages": [
                {"role": "system", "content": "你是一个图片理解助手。请用中文分析图片。如果有文字，提取关键文字；如果没有文字，描述主体、场景、关系、图表趋势或异常点。"},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "请解析这张图片，兼顾文字识别和画面理解，控制在300字以内。"},
                ]},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        }
        try:
            with httpx.Client(timeout=40.0, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            logger.warning("图片视觉描述 HTTP 失败 status=%s body=%s", exc.response.status_code if exc.response is not None else None, body, exc_info=True)
            return ""
        except Exception as exc:
            logger.warning("图片视觉描述失败: %s", exc, exc_info=True)
            return ""
