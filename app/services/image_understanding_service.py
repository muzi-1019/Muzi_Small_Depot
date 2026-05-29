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

import base64  # Base64 编解码，用于处理前端传来的图片 dataURL/base64
import io  # 字节流工具，用于把 bytes 包装成图片文件对象
import logging  # 日志模块
import re  # 正则模块，用于解析 dataURL 头部 MIME

import httpx  # HTTP 客户端，用于调用视觉模型 API
from PIL import Image  # Pillow 图片处理库，用于 OCR 前打开图片

from app.core.config import settings  # 全局配置，包含 OpenAI 兼容接口和视觉模型名称

logger = logging.getLogger(__name__)  # 当前模块日志器


class ImageUnderstandingService:
    """图片理解服务：把用户上传的图片转换成可被大模型使用的中文文本上下文"""

    @staticmethod
    def analyze(image_data: str | None, image_mime: str | None = None) -> str:
        """解析用户上传图片，返回 OCR 文本和视觉描述组成的多模态上下文"""
        image_bytes, mime = ImageUnderstandingService._decode_image(image_data, image_mime)  # 解码图片数据和 MIME 类型
        if not image_bytes:  # 如果没有得到有效图片字节
            if image_data:  # 如果用户确实传了图片数据但解析失败
                logger.warning("图片解析跳过：图片数据解码为空 mime=%s data_len=%d", image_mime, len(image_data))
            return ""  # 没有图片上下文可返回
        logger.info("开始图片解析 mime=%s bytes=%d", mime, len(image_bytes))
        ocr_text = ImageUnderstandingService._ocr_image(image_bytes)  # OCR 识别图片中的文字
        vision_desc = ImageUnderstandingService._describe_image(image_bytes, mime)  # 调用视觉模型生成图片描述
        parts: list[str] = []  # 保存最终多模态上下文片段
        if ocr_text:  # 如果 OCR 识别到文字
            parts.append(f"【图片OCR文字】\n{ocr_text}")
        if vision_desc:  # 如果视觉模型返回描述
            parts.append(f"【图片视觉描述】\n{vision_desc}")
        if not parts:  # OCR 和视觉描述都为空
            parts.append("【图片解析结果】已收到图片，但未识别到明确文字或可描述内容。")
        logger.info("图片解析完成 mime=%s ocr_len=%d vision_len=%d context_len=%d", mime, len(ocr_text), len(vision_desc), len("\n\n".join(parts)))
        return "\n\n".join(parts)  # 用空行拼接 OCR 与视觉描述

    @staticmethod
    def _decode_image(image_data: str | None, image_mime: str | None = None) -> tuple[bytes, str]:
        """解码前端传来的 dataURL/base64 图片，并返回图片字节和 MIME 类型"""
        if not image_data:  # 没有传图片
            return b"", image_mime or "image/png"  # 返回空字节和默认 MIME
        data = image_data.strip()  # 去除首尾空白，避免影响 base64 解码
        mime = image_mime or "image/png"  # 优先使用调用方传入 MIME，否则默认 PNG
        if data.startswith("data:"):  # 前端可能传 data:image/png;base64,... 格式
            header, _, payload = data.partition(",")  # 拆分 dataURL 头部和真正 base64 内容
            match = re.match(r"data:([^;]+);base64", header)  # 从头部提取 MIME 类型
            if match:  # 如果匹配成功
                mime = match.group(1)  # 更新 MIME
            data = payload  # 去掉 dataURL 头，仅保留 base64 内容
        try:  # base64 解码可能失败
            raw = base64.b64decode(data, validate=False)  # 解码图片原始字节
        except Exception as exc:
            logger.warning("图片 base64 解码失败 mime=%s data_len=%d error=%s", mime, len(data), exc)
            return b"", mime
        return raw, mime  # 返回图片字节和 MIME

    @staticmethod
    def _ocr_image(image_bytes: bytes) -> str:
        """使用 RapidOCR 识别图片文字；未安装 OCR 依赖时自动跳过"""
        try:  # OCR 依赖可能未安装
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            logger.warning("图片 OCR 跳过：未安装 rapidocr_onnxruntime error=%s", exc)
            return ""
        try:  # 图片打开或 OCR 推理可能失败
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")  # 从字节打开图片并转为 RGB
            result, _ = RapidOCR()(image)  # 执行 OCR，result 包含检测框和文字等信息
            if not result:  # 如果没有识别结果
                return ""  # 返回空字符串
            lines = [str(line[1]).strip() for line in result if len(line) > 1 and str(line[1]).strip()]  # 提取每行识别文字
            return "\n".join(lines)  # 按行拼接 OCR 文本
        except Exception as exc:
            logger.warning("图片 OCR 失败: %s", exc, exc_info=True)
            return ""

    @staticmethod
    def _describe_image(image_bytes: bytes, mime: str = "image/png") -> str:
        """调用 OpenAI 兼容视觉模型描述图片内容，适合无文字图片、图表、照片"""
        base_url = (settings.openai_api_base or "").rstrip("/")  # 读取 OpenAI 兼容 API 地址
        api_key = settings.openai_api_key or ""  # 读取 API Key
        if not base_url or not api_key:  # 缺少配置时不能调用视觉模型
            logger.warning("图片视觉描述跳过：openai_api_base 或 openai_api_key 未配置")
            return ""
        b64 = base64.b64encode(image_bytes).decode("utf-8")  # 将图片字节重新编码为 base64，放入请求体
        url = f"{base_url}/chat/completions"  # 构造视觉模型聊天接口地址
        headers = {"Authorization": f"Bearer {api_key}"}  # 设置鉴权头
        payload = {
            "model": settings.vision_model_name,  # 使用配置中的视觉模型
            "messages": [
                {"role": "system", "content": "你是一个图片理解助手。请用中文分析图片。如果有文字，提取关键文字；如果没有文字，描述主体、场景、关系、图表趋势或异常点。"},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "请解析这张图片，兼顾文字识别和画面理解，控制在300字以内。"},
                ]},
            ],
            "temperature": 0.2,  # 较低温度，保证图片描述更稳定
            "max_tokens": 400,  # 限制描述长度
        }  # 完成视觉模型请求体
        try:  # 外部视觉 API 可能超时或返回错误
            with httpx.Client(timeout=40.0, trust_env=False) as client:  # 创建 HTTP 客户端
                resp = client.post(url, headers=headers, json=payload)  # 发送请求
                resp.raise_for_status()  # 非 2xx 状态码抛异常
                data = resp.json()  # 解析响应 JSON
            return data["choices"][0]["message"]["content"].strip()  # 提取模型回复文本
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            logger.warning("图片视觉描述 HTTP 失败 status=%s body=%s", exc.response.status_code if exc.response is not None else None, body, exc_info=True)
            return ""
        except Exception as exc:
            logger.warning("图片视觉描述失败: %s", exc, exc_info=True)
            return ""
