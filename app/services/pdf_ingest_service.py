"""
本文件的作用：PDF 解析与向量入库服务（RAG 知识管道的核心）。
完整处理流程：
1. 读取 PDF 文件，提取文字内容（支持 PyMuPDF 和 pypdf 两种库，支持 OCR 扫描件识别）
2. 将长文本切分成固定大小的片段（chunks），带有重叠区域避免信息断裂
3. 调用 Embedding API 将每个文本片段转换为向量（数字表示的含义）
4. 将向量和文本写入 Milvus 向量数据库，供后续 RAG 检索使用

同时提供搜索和查询功能：
- search：根据问题搜索最相关的文本片段
- has_data：检查某个角色是否已有知识库数据
"""

from __future__ import annotations  # 允许在类型注解中使用字符串形式的类型

import hashlib                    # 哈希算法库，用于生成文本指纹和备用向量
import logging                    # 日志
import re                         # 正则表达式，用于文本清洗
import time                       # 计时
from collections import OrderedDict  # 有序字典，用于 LRU 缓存
from dataclasses import dataclass  # 数据类装饰器
from pathlib import Path           # 文件路径处理
from functools import lru_cache     # 缓存工具

logger = logging.getLogger(__name__)  # 获取当前模块的日志器，统一输出入库、检索、失败回退等日志

# ========== Embedding 查询缓存（LRU，最多缓存 512 条） ==========
_EMBED_CACHE_MAX = 512  # embedding 内存缓存容量上限，防止长时间运行时无限占用内存
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()  # key 为文本摘要，value 为 embedding 向量

# ========== BM25 文档缓存（避免每次重新拉取 Milvus） ==========
_bm25_cache: dict[int, dict] = {}  # character_id -> {rows, doc_tokens, avgdl, ts}

# ========== 本地 Reranker 单例缓存（避免每次请求重复加载模型权重） ==========
_local_reranker = None        # CrossEncoder 模型单例
_local_reranker_path: str = ""  # 已加载模型的路径，路径变更时触发重新加载

# ========== 本地 Embedder 单例缓存（避免每次请求重复加载模型权重） ==========
_local_embedder = None        # SentenceTransformer 模型单例
_local_embedder_path: str = ""  # 已加载模型的路径，路径变更时触发重新加载

from app.core.config import settings  # 全局配置


@dataclass
class IngestedChunk:
    """已处理的文本片段数据结构"""
    text: str               # 文本内容
    vector: list[float]     # 向量表示（数字数组）
    chunk_index: int        # 片段序号
    page_start: int         # 起始页码
    page_end: int           # 结束页码
    keywords: str = ""      # 关键词串（用于混合检索）


class PDFIngestService:
    """PDF 解析与向量入库服务：负责将 PDF 文件转化为可检索的向量知识"""

    def __init__(self) -> None:
        self.collection_prefix = settings.milvus_collection  # Milvus 集合名前缀

    @staticmethod
    def _collection_for(character_id: int) -> str:
        """根据角色ID生成独立的 Milvus 集合名（每个角色一个集合，完全隔离）"""
        return f"{settings.milvus_collection}_{character_id}"  # 例如 character_knowledge_6，避免不同角色知识混在一起

    def ingest_all(self) -> dict[str, int]:
        """批量入库：根据预定义的角色-PDF映射关系，将所有PDF文件解析并写入向量库"""
        mapping = self._role_pdf_mapping()  # 获取内置的角色 ID 与 PDF 路径映射
        inserted = 0  # 统计最终写入 Milvus 的 chunk 总数
        scanned = 0  # 统计本次扫描处理了多少个 PDF 文件
        for character_id, pdf_path in mapping.items():  # 逐个角色、逐个文件执行入库
            scanned += 1  # 每处理一个映射项，扫描计数加一
            inserted += self.ingest_file(character_id, pdf_path)  # 调用单文件入库，并累计写入条数
        return {"scanned": scanned, "inserted": inserted}  # 返回批处理统计结果，方便接口或脚本展示

    def ingest_file(self, character_id: int, pdf_path: Path) -> int:
        """单文件入库：解析指定PDF → 切分文本 → 向量化 → 写入Milvus，返回写入的向量条数"""
        if not pdf_path.exists():  # 如果文件路径不存在，直接跳过，避免后续打开文件时报错
            return 0  # 返回 0 表示没有任何 chunk 被写入
        logger.info("[PDF ingest] start character_id=%s file=%s", character_id, pdf_path)  # 记录入库开始日志，便于排查慢文件
        # 先将 PDF 中的文字、表格、OCR 内容和图片描述统一抽取为纯文本。
        text = self._extract_text(pdf_path)  # 调用 PDF 抽取函数，得到整份 PDF 的可检索文本
        # 再按固定窗口切分为 chunk，避免一次 embedding 输入过长，也方便后续精确召回。
        chunks = self._chunk_text(text)  # 将长文本切成多个较短片段，每个片段后续生成一个向量
        logger.info("[PDF ingest] extracted chars=%d chunks=%d file=%s", len(text), len(chunks), pdf_path.name)  # 记录抽取字符数和切块数量
        if not chunks:  # 如果没有有效文本片段，说明文件为空或解析失败
            return 0  # 不写入 Milvus，直接返回 0
        rows = []  # 保存待批量插入 Milvus 的行数据
        # 每个 chunk 构造成一条 Milvus 记录：包含文本、关键词、向量、来源文件和哈希指纹。
        for chunk_index, chunk in enumerate(chunks):  # 遍历每个文本片段并生成入库记录
            if chunk_index % 20 == 0:  # 每处理 20 个 chunk 打一次日志，避免日志过多但又能看到进度
                logger.info("[PDF ingest] embedding progress %d/%d file=%s", chunk_index, len(chunks), pdf_path.name)  # 输出 embedding 进度
            rows.append(self._build_row(character_id, pdf_path, chunk, chunk_index))  # 构建单条 Milvus 数据并加入批量列表
        logger.info("[PDF ingest] inserting rows=%d character_id=%s", len(rows), character_id)  # 记录即将插入的行数
        self._insert_into_milvus(rows, character_id)  # 执行 Milvus 批量写入
        logger.info("[PDF ingest] done rows=%d character_id=%s file=%s", len(rows), character_id, pdf_path.name)  # 记录入库完成日志
        return len(rows)  # 返回写入的 chunk 数量

    def ingest_text(self, character_id: int, source_name: str, text: str) -> int:
        """纯文本入库：复用 PDF 入库的切分、向量化和 Milvus 写入流程，支持 txt/docx 等非 PDF 数据源。"""
        if not text.strip():  # 如果上传文本去除空白后为空，说明没有可入库内容
            return 0  # 返回 0 表示未写入任何向量
        chunks = self._chunk_text(text)  # 复用 PDF 的文本切分逻辑，保证 chunk 粒度一致
        logger.info("[Text ingest] extracted chars=%d chunks=%d source=%s", len(text), len(chunks), source_name)  # 记录纯文本入库统计
        if not chunks:  # 如果清洗切分后没有有效片段
            return 0  # 跳过 Milvus 写入
        source_path = Path(source_name)  # 将来源名称包装为 Path，便于复用 _build_row 中的文件名逻辑
        rows = []  # 保存待写入 Milvus 的文本 chunk 行
        # 非 PDF 文件没有页码信息，因此用 source_name 作为来源文件名，其他字段与 PDF chunk 保持一致。
        for chunk_index, chunk in enumerate(chunks):  # 遍历纯文本切出的所有 chunk
            if chunk_index % 20 == 0:  # 每 20 个 chunk 输出一次 embedding 进度
                logger.info("[Text ingest] embedding progress %d/%d source=%s", chunk_index, len(chunks), source_name)  # 记录当前处理位置
            rows.append(self._build_row(character_id, source_path, chunk, chunk_index))  # 构造一条可入库的向量记录
        logger.info("[Text ingest] inserting rows=%d character_id=%s source=%s", len(rows), character_id, source_name)  # 记录写入前统计
        self._insert_into_milvus(rows, character_id)  # 将纯文本 chunk 批量写入当前角色的 Milvus collection
        logger.info("[Text ingest] done rows=%d character_id=%s source=%s", len(rows), character_id, source_name)  # 记录入库完成
        return len(rows)  # 返回实际写入的 chunk 数

    def _role_pdf_mapping(self) -> dict[int, Path]:
        """预定义的角色ID与PDF文件的映射关系（硬编码的初始知识库配置）"""
        data_dir = Path(settings.data_dir)  # 从配置中读取数据目录，并转成 Path 便于拼接文件路径
        return {
            2: data_dir / "data/国家基层高血压防治管理手册2025版.pdf",  # 角色 2 对应的默认知识库 PDF
            3: data_dir / "data/中华人民共和国宪法.pdf",  # 角色 3 对应的默认知识库 PDF
        }  # 返回硬编码映射，主要用于初始化或脚本批量导入

    def _extract_text(self, pdf_path: Path) -> str:
        """从PDF文件中提取全部文字内容（优先用PyMuPDF，不可用时用pypdf，扫描件用OCR识别）。
        增强：自动检测表格并转为 Markdown 格式，保留表格结构信息用于精准检索。
        """
        try:  # 优先尝试使用 PyMuPDF，因为它支持文本、表格、图片和页面渲染等更多能力
            import fitz  # PyMuPDF
        except ImportError:  # 如果当前环境没有安装 PyMuPDF 或者报错，则退化为 pypdf 文本抽取
            from pypdf import PdfReader  # 导入 pypdf 的 PDF 读取器
            reader = PdfReader(str(pdf_path))  # 打开 PDF 文件并创建读取对象
            return "\n".join(page.extract_text() or "" for page in reader.pages)  # 逐页提取文本并拼接返回

        doc = fitz.open(str(pdf_path))  # 使用 PyMuPDF 打开 PDF，后续可逐页读取文本、表格和图片
        ocr_engine = None  # OCR 引擎延迟初始化，只有遇到疑似扫描页时才创建
        pages: list[str] = []  # 保存每一页抽取后的文本，最后统一拼接成整篇文档
        total_pages = len(doc)  # 获取总页数，用于日志展示解析进度
        # 逐页解析，按“文本层 → OCR 兜底 → 表格结构 → 图片描述”的顺序补全页面内容。
        for page_index, page in enumerate(doc, 1):  # 从第 1 页开始遍历 PDF 页面
            logger.info("[PDF ingest] parsing page %d/%d file=%s", page_index, total_pages, pdf_path.name)  # 输出当前页解析进度
            # 表格单独提取为 Markdown，避免普通文本提取破坏行列结构。
            table_md = self._extract_tables_as_markdown(page)  # 尝试将页面中的表格抽取为 Markdown 文本
            text = page.get_text("text") or ""  # 提取页面文本层；如果没有文本层则得到空字符串
            # 如果文本层过少，通常说明是扫描件或图片型 PDF，此时再启用 OCR。
            if len(text.strip()) < 30:  # 文本太少时，判断页面可能是扫描件或图片页
                if ocr_engine is None:  # 如果 OCR 引擎还没有初始化
                    ocr_engine = self._get_ocr_engine()  # 创建 OCR 引擎；未安装依赖时会返回 None
                ocr_text = self._ocr_page(page, ocr_engine)  # 对当前页面做 OCR 识别
                text = ocr_text if ocr_text else text  # OCR 有结果则替换文本层，否则保留原文本
            # 图片内容通过视觉模型转成文字描述，让图表/截图也能进入向量检索。
            image_desc = self._extract_images_as_text(page)  # 提取页面图片并生成文字描述
            if table_md:  # 如果页面中检测到了表格
                text = text + "\n\n" + table_md  # 将表格 Markdown 追加到页面文本末尾
            if image_desc:  # 如果页面图片生成了有效描述
                text = text + "\n\n" + image_desc  # 将图片描述追加到页面文本末尾
            pages.append(text)  # 保存当前页最终整合后的文本
        doc.close()  # 关闭 PDF 文件句柄，释放系统资源
        return "\n".join(pages)  # 将所有页面文本按换行拼接为整份文档文本

    def _extract_images_as_text(self, page) -> str:
        """从PDF页面中提取图像，使用多模态视觉模型生成文字描述，使图像内容可被语义检索。
        仅处理面积较大的图像（>100x100像素），避免处理小图标和装饰元素。
        """
        try:  # 图片提取依赖 PyMuPDF 页面对象，失败时直接跳过图片处理
            import fitz  # 保留 PyMuPDF 导入，确保当前环境具备图片提取能力
            images = page.get_images(full=True)  # 获取当前页面中的所有图片引用信息
            if not images:  # 如果页面没有图片
                return ""  # 返回空字符串，表示没有图片描述
        except Exception:  # 图片枚举失败时不影响 PDF 主体文本入库
            return ""  # 静默跳过图片处理
        descriptions: list[str] = []  # 保存每张图片生成的文字描述
        doc = page.parent  # 获取 PDF 文档对象，用于通过 xref 提取图片二进制数据
        for img_index, img_info in enumerate(images[:3], 1):  # 只处理前 3 张图片，并从 1 开始编号
            # 每页最多处理前 3 张图片，控制视觉模型调用次数，避免入库耗时和成本失控。
            try:  # 单张图片处理失败时只跳过该图片，不影响其他图片
                xref = img_info[0]  # 取出图片在 PDF 内部的 xref 引用编号
                # xref 是 PDF 内部图片引用 ID
                # 通过它可以提取图片二进制数据
                base_image = doc.extract_image(xref)  # 根据 xref 从 PDF 中提取图片原始数据
                if not base_image:  # 如果提取结果为空，说明该引用无法得到有效图片
                    continue  # 跳过当前图片
                width = base_image.get("width", 0)  # 读取图片宽度，缺失时默认为 0
                height = base_image.get("height", 0)  # 读取图片高度，缺失时默认为 0
                if width < 100 or height < 100:  # 过滤过小图片，避免把图标/装饰元素送给视觉模型
                    # 小图通常是 logo、icon、装饰图或页眉页脚元素，对 RAG 检索价值较低，直接跳过。
                    continue
                image_bytes = base_image["image"]  # 获取图片二进制内容
                ext = base_image.get("ext", "png")  # 获取图片格式，缺失时默认按 png 处理
                logger.info("[PDF ingest] describing image %d/%d size=%sx%s", img_index, min(len(images), 3), width, height)  # 记录视觉描述调用信息
                desc = self._describe_image(image_bytes, ext)  # 调用视觉模型生成中文描述
                if desc:  # 如果视觉模型返回了非空描述
                    descriptions.append(f"[图像内容描述] {desc}")  # 加上统一前缀，便于后续检索和上下文识别
            except Exception:  # 单张图片处理异常时继续处理下一张
                continue  # 跳过当前异常图片
        return "\n".join(descriptions)  # 将所有图片描述合并为一段文本返回

    @staticmethod
    def _describe_image(image_bytes: bytes, ext: str = "png") -> str:
        """调用配置中的视觉模型"deepseek-ai/DeepSeek-OCR"，为 PDF 中提取出的图片生成中文描述。
        图片会被编码为 base64 data URL，并通过 OpenAI 兼容的 chat/completions 接口发送。
        如果外部接口调用失败，则返回空字符串，不中断 PDF 入库流程。
        """
        import base64  # 用于把图片二进制编码为可放入 JSON 的 base64 字符串
        base_url = (settings.openai_api_base or "").rstrip("/")  # 读取 OpenAI 兼容接口地址，并去掉末尾斜杠
        api_key = settings.openai_api_key or ""  # 读取 API Key，没有配置时为空字符串
        if not base_url or not api_key:  # 如果缺少接口地址或密钥，就无法调用视觉模型
            return ""  # 返回空描述，保证图片理解失败不影响主入库流程
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, f"image/{ext}")  # 根据图片后缀映射 MIME 类型
        # 大图（全页扫描可达 1800×2100）base64 后体积高达 5-8MB，会触发写超时；先压缩到 ≤1024px 再编码
        try:
            from PIL import Image as _PILImage
            import io as _io
            img = _PILImage.open(_io.BytesIO(image_bytes))
            w, h = img.size
            max_side = 1024
            if max(w, h) > max_side:  # 仅在超过阈值时缩放，避免对小图二次编码损耗
                scale = max_side / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), _PILImage.LANCZOS)
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80)  # 转 JPEG 进一步压缩
            image_bytes = buf.getvalue()
            mime = "image/jpeg"
        except Exception:
            pass  # PIL 不可用或处理失败时使用原始图片
        b64 = base64.b64encode(image_bytes).decode("utf-8")  # 将图片二进制编码为 UTF-8 字符串

        url = f"{base_url}/chat/completions"  # 构造视觉模型使用的聊天补全接口地址
        headers = {"Authorization": f"Bearer {api_key}"}  # 设置 Bearer Token 鉴权头
        payload = {
            "model": settings.vision_model_name,  # 使用配置中的视觉模型名称
            "messages": [  # OpenAI 兼容 chat/completions 消息体
                {"role": "system", "content": "你是一个图像分析助手。请用中文简洁描述图像中的关键信息，包括但不限于图表类型、数据趋势、组织结构等。不超过200字。"},  # 系统提示词，限制输出语言和长度
                {"role": "user", "content": [  # 用户消息同时包含图片和文本指令
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},  # 用 data URL 形式直接传入图片
                    {"type": "text", "text": "请描述这张图片的内容。"},  # 要求模型生成图片内容描述
                ]},  # 结束用户多模态消息
            ],  # 结束 messages 列表
            "temperature": 0.2,  # 使用较低随机性，保证图片描述稳定
            "max_tokens": 300,  # 限制最大输出长度，避免图片描述过长影响入库
        }  # 完成请求体构造
        try:  # 调用外部视觉模型时要捕获异常，避免图片失败影响整个 PDF 入库
            import httpx  # HTTP 客户端库
            _timeout = httpx.Timeout(connect=10.0, write=30.0, read=60.0, pool=5.0)  # 拆分超时：写 30s（大图上传），读 60s（模型推理）
            with httpx.Client(timeout=_timeout, trust_env=False) as client:  # 创建客户端，分别设置写和读超时
                resp = client.post(url, headers=headers, json=payload)  # 发送视觉模型请求
                resp.raise_for_status()  # 非 2xx 状态码直接抛出异常
                data = resp.json()  # 解析 JSON 响应
            return data["choices"][0]["message"]["content"].strip()  # 提取模型返回文本并去除首尾空白
        except Exception as e:  # 捕获网络、解析、模型服务等所有异常
            logger.debug("图像描述失败: %s", e)  # 使用 debug 记录失败原因，避免正常入库日志过吵
            return ""  # 返回空字符串，让上层跳过该图片描述

    @staticmethod
    def _extract_tables_as_markdown(page) -> str:
        """从PDF页面中检测并提取表格，转换为 Markdown 格式以保留结构信息。
        利用 PyMuPDF 的 find_tables() API 自动识别表格边界和单元格。
        """
        try:  # 表格检测依赖 PyMuPDF 的 find_tables，部分版本或页面可能不支持
            tabs = page.find_tables()  # 自动检测当前页面中的表格区域
            if not tabs or not tabs.tables:  # 如果没有检测到表格
                return ""  # 返回空字符串，不向页面文本追加表格内容
        except Exception:  # 表格检测失败时跳过，避免影响普通文本解析
            return ""  # 返回空字符串表示无可用表格
        parts: list[str] = []  # 保存每个表格转换后的 Markdown 文本
        for table in tabs.tables:  # 遍历当前页面检测到的每个表格
            try:  # 单个表格抽取失败时只跳过该表格
                data = table.extract()  # 抽取表格单元格二维数组
                if not data or len(data) < 1:  # 如果表格没有任何行
                    continue  # 跳过该表格
                # 第一行作为表头，其余行作为表体，统一清理换行符后拼成 Markdown 表格。
                headers = [str(cell or "").strip().replace("\n", " ") for cell in data[0]]  # 清理表头单元格中的空值和换行
                if not any(headers):  # 如果表头全为空，说明表格结构质量太差
                    continue  # 跳过该表格，避免生成无意义 Markdown
                md_lines = ["| " + " | ".join(headers) + " |"]  # 生成 Markdown 表头行
                md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")  # 生成 Markdown 分隔行
                for row in data[1:]:  # 遍历表体行
                    cells = [str(cell or "").strip().replace("\n", " ") for cell in row]  # 清理每个单元格文本
                    md_lines.append("| " + " | ".join(cells) + " |")  # 追加 Markdown 表格行
                parts.append("\n".join(md_lines))  # 将当前表格 Markdown 加入结果列表
            except Exception:  # 单个表格异常不影响同页其他表格
                continue  # 跳过当前异常表格
        return "\n\n".join(parts)  # 多个表格之间用空行分隔后返回

    @staticmethod
    def _get_ocr_engine():
        """获取OCR引擎实例（用于识别扫描版PDF中的图片文字）。
        这里选择 RapidOCR 而不是直接依赖云端 OCR：
        1. 可离线运行，不会把用户上传的 PDF 图片传到第三方 OCR 平台，隐私风险更低；
        2. 基于 ONNX Runtime，部署比 PaddleOCR 更轻量，Windows 本地环境更容易安装；
        3. 对中文扫描件、表格截图中的普通文本识别效果足够支撑 RAG 入库。
        如果未安装 rapidocr_onnxruntime，会自动跳过 OCR，不影响普通文字版 PDF 的解析流程。
        """
        try:  # 尝试导入 RapidOCR，本地环境安装后即可使用
            from rapidocr_onnxruntime import RapidOCR  # 轻量 OCR 引擎
            return RapidOCR()  # 创建 OCR 实例，供扫描页识别使用
        except ImportError:  # 未安装 OCR 依赖时进入兜底分支
            return None  # 返回 None 表示 OCR 不可用，上层会自动跳过 OCR

    @staticmethod
    def _ocr_page(page, ocr_engine) -> str | None:
        """对单个PDF页面进行OCR文字识别（当普通文本提取结果太少时使用）。
        只在页面文字少于阈值时触发 OCR，而不是每页都 OCR：
        - 普通 PDF 的文本层提取速度远快于 OCR，直接提取即可；
        - OCR 成本更高且可能产生识别误差，作为扫描件兜底更合适；
        - 2 倍缩放渲染能提升小字识别率，同时不会像 3~4 倍那样显著增加内存和耗时。
        """
        if ocr_engine is None:  # 如果上层没有传入可用 OCR 引擎
            try:  # 尝试在函数内部临时创建 OCR 引擎
                from rapidocr_onnxruntime import RapidOCR  # 导入 OCR 类
                ocr_engine = RapidOCR()  # 初始化 OCR 实例
            except ImportError:  # 如果依赖不存在
                return None  # 返回 None 表示无法 OCR
        try:  # OCR 涉及页面渲染、图片转换和模型推理，任何一步都可能失败
            import fitz  # PyMuPDF
            from PIL import Image  # Pillow 用于打开渲染后的图片
            import io  # io.BytesIO 用于在内存中包装图片字节
            mat = fitz.Matrix(2.0, 2.0)  # 以 2 倍缩放渲染页面，提高小字识别率
            pix = page.get_pixmap(matrix=mat)  # 将 PDF 页面渲染为位图
            img = Image.open(io.BytesIO(pix.tobytes("png")))  # 将位图字节转为 PIL Image
            result, _ = ocr_engine(img)  # 调用 OCR 引擎识别图片文字
            if result:  # 如果 OCR 返回了识别行
                return "\n".join(line[1] for line in result)  # 提取每行文字并按换行拼接
        except Exception:  # OCR 失败时不抛出异常
            pass  # 静默失败，交给上层保留原始文本
        return None  # 没有 OCR 结果时返回 None

    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
        """将长文本切分成固定大小的片段，相邻片段之间有重叠区域（避免关键信息被切断）。
        选择 800 字符作为默认 chunk_size：
        - 比 300~500 字符更容易保留完整段落、表格行和上下文关系；
        - 比 1500+ 字符更不容易混入多个主题，向量表示更集中，检索命中更精准；
        - 对中文招股说明书这类长文档，800 字符在“语义完整性”和“检索粒度”之间较均衡。
        选择 120 字符 overlap：
        - 约 15% 重叠率，能防止股东名称、金额、比例等关键信息刚好被切在边界；
        - 相比 0 重叠召回更稳，相比 300+ 重叠又不会明显放大 Milvus 存储和检索成本。
        对比了 更粗粒度（1024/150） 和 更细粒度（512/80） 的分块策略：
        1024/150：分块太大，会使语义稀释，不能更好的表达语义
        512/80：分块太小，会让语义截断，让上下文不完整
        """
        cleaned = re.sub(r"\s+", " ", text).strip()  # 将连续空白、换行、制表符统一压缩为单个空格
        if not cleaned:  # 如果清洗后文本为空
            return []  # 返回空列表，表示没有可切分内容
        chunks: list[str] = []  # 保存切分后的文本片段
        start = 0  # 当前滑动窗口的起始位置
        # 滑动窗口切分：每次前进 chunk_size - overlap，保留边界上下文。
        while start < len(cleaned):  # 只要起始位置还没有到文本末尾，就继续切分
            end = min(len(cleaned), start + chunk_size)  # 计算当前 chunk 结束位置，不能超过文本总长度
            chunks.append(cleaned[start:end].strip())  # 截取当前窗口文本并去除首尾空白
            if end >= len(cleaned):  # 如果已经切到全文末尾
                break  # 退出循环
            start = max(end - overlap, start + 1)  # 下一个窗口回退 overlap 字符，保留上下文重叠
        return [c for c in chunks if c]  # 过滤空字符串后返回最终 chunk 列表

    def _build_row(self, character_id: int, pdf_path: Path, chunk_text: str, chunk_index: int = 0) -> dict[str, object]:
        """为单个文本片段构建完整的数据行（来源文件、文本、向量、哈希指纹，角色通过独立集合隔离）"""
        keywords = self._extract_keywords(chunk_text)  # 从 chunk 中抽取关键词，供 BM25 检索使用
        return {
            "source_file": pdf_path.name,  # 来源 PDF 文件名，用于回答时展示知识出处
            "chunk_index": chunk_index,  # 当前片段在原文中的序号，用于定位和排序
            "text": chunk_text,  # 实际入库的文本片段，是后续 RAG 返回给大模型的核心上下文
            "keywords": keywords,  # 从 chunk 中提取的关键词，用于 BM25 关键词检索增强
            "vector": self._embed(chunk_text),  # chunk 的语义向量，用于 Milvus ANN 向量相似度检索
            "chunk_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),  # 文本指纹，用于去重和避免重复入库
        }

    def search_dispatch(self, character_id: int, query: str, top_k: int | None = None, mode: str | None = None) -> list[dict[str, object]]:
        """统一检索调度：根据 mode 参数路由到 vector / keyword(BM25) / hybrid 检索。
        mode 为 None 时使用 settings.retrieval_mode 默认值。
        """
        mode = (mode or settings.retrieval_mode).lower()  # 优先使用调用方传入模式，否则使用配置中的默认检索模式
        if mode == "vector":  # 如果指定向量模式
            return self.search_vector(character_id, query, top_k)  # 只执行 Milvus 向量召回
        elif mode == "keyword":  # 如果指定关键词模式
            return self.search_keyword(character_id, query, top_k)  # 只执行 BM25 关键词召回
        # 默认走 hybrid：BM25 + Milvus 向量 + Neo4j 图谱三路召回。
        else:
            return self.search_hybrid(character_id, query, top_k)  # 执行三路混合召回

    def search(self, character_id: int, query: str, top_k: int | None = None) -> list[str]:
        """在Milvus中搜索与用户问题最相关的文本片段（用于RAG检索）"""
        rows = self.search_dispatch(character_id, query, top_k=top_k)  # 调用统一检索入口获取带元数据的结果
        return [row["text"] for row in rows]  # 只提取 text 字段，兼容只需要上下文文本的调用方

    def search_with_meta(self, character_id: int, query: str, top_k: int | None = None, mode: str | None = None) -> list[dict[str, object]]:
        """在Milvus中搜索并返回带元数据的知识片段（用于参考文献展示）。
        返回列表中的每个字典包含：source_file, chunk_index, score, text, method。
        """
        return self.search_dispatch(character_id, query, top_k=top_k, mode=mode)  # 保留完整 row 元数据，供引用来源展示

    def has_data(self, character_id: int) -> bool:
        """检查指定角色在Milvus中是否已有向量数据（每个角色独立集合）"""
        from pymilvus import Collection, connections, utility
        try:
            # 仅检查 Milvus collection 是否存在且有数据，不做真实检索。
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        except Exception:  # 连接 Milvus 失败时认为没有可用数据
            return False  # 返回 False，避免 RAG 主流程抛错
        coll_name = self._collection_for(character_id)  # 生成当前角色对应的 collection 名
        if not utility.has_collection(coll_name):  # 如果该角色 collection 不存在
            return False  # 表示该角色还没有入库数据
        collection = Collection(coll_name)  # 获取 Milvus collection 对象
        collection.load()  # 加载 collection 到内存，才能读取实体数量
        return collection.num_entities > 0  # 判断 collection 中是否至少有一条向量记录

    @staticmethod
    @lru_cache(maxsize=1)
    def _stopwords() -> set[str]:
        words = {  # 常见停用词集合，避免 BM25/关键词提取被无意义词干扰
            "的", "了", "和", "是", "在", "也", "就", "都", "而", "及", "与", "着", "或", "一个", "我们", "你们", "他们", "以及",
            "什么", "怎么", "如何", "可以", "是否", "有没有", "请问", "帮我", "告诉我", "对于", "这个", "那个",
        }
        return words  # 返回缓存后的停用词集合

    def _extract_keywords(self, text: str, top_n: int = 8) -> str:
        """为chunk提取关键词，供关键词检索使用。"""
        try:  # 优先使用 jieba 的关键词抽取能力
            import jieba.analyse  # jieba.analyse 提供 TF-IDF 等关键词抽取方法
            # 优先使用 jieba TF-IDF 关键词抽取，适合中文长文本 chunk。
            keywords = jieba.analyse.extract_tags(text, topK=top_n)
            if keywords:
                return " ".join(k.strip() for k in keywords if k.strip())
        except Exception:  # jieba 不可用或抽取失败时走正则兜底
            pass  # 不抛错，继续执行后面的 fallback
        # jieba 不可用时退化为正则分词，并过滤常见停用词。
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text)
        stopwords = self._stopwords()
        tokens = [t for t in tokens if t not in stopwords and len(t) > 1]
        return " ".join(tokens[:top_n])  # 截取前 top_n 个 token 并用空格拼接为关键词字符串

    def _tokenize_query(self, query: str) -> list[str]:
        """对查询文本分词，用于关键词检索。"""
        try:  # 查询分词优先使用 jieba，中文切分效果更好
            import jieba  # 导入中文分词库
            tokens = [t.strip() for t in jieba.lcut(query) if t.strip()]  # 对查询分词并过滤空 token
        except Exception:  # jieba 不可用时使用正则兜底
            tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", query)  # 抽取中文、英文、数字组成的连续片段
        stopwords = self._stopwords()  # 获取停用词集合
        return [t for t in tokens if t not in stopwords and len(t) > 1]  # 过滤停用词和单字符噪声词

    @staticmethod
    def _normalize_score(score: float, min_score: float, max_score: float) -> float:
        if max_score <= min_score:  # 如果最大值不大于最小值，说明无法做有效归一化
            return 0.0  # 返回 0，避免除零或所有分数相同造成异常
        return (score - min_score) / (max_score - min_score)  # 将原始分数线性归一化到 0~1 区间

    def _get_bm25_cache(self, character_id: int) -> dict:
        """获取 BM25 文档缓存，每 300 秒刷新一次"""
        import time as _time  # 使用局部别名，避免和模块顶部 time 名称混淆
        cached = _bm25_cache.get(character_id)  # 从全局缓存中读取当前角色的 BM25 数据
        if cached and (_time.time() - cached.get("ts", 0)) < 300:  # 如果缓存存在且未超过 300 秒
            return cached  # 直接返回缓存，避免重复查询 Milvus
        from pymilvus import Collection, connections, utility  # 导入 Milvus 连接、集合和工具方法
        try:
            # BM25 需要完整候选文本，因此从 Milvus 拉取文本字段后在内存中计算分数。
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)  # 连接 Milvus 服务
        except Exception:  # 连接失败时不能构建 BM25 缓存
            return {}  # 返回空字典，上层会跳过关键词召回
        coll_name = self._collection_for(character_id)  # 得到当前角色的 Milvus collection 名
        if not utility.has_collection(coll_name):  # 如果 collection 不存在
            return {}  # 返回空缓存
        collection = Collection(coll_name)  # 创建 collection 对象
        collection.load()  # 加载 collection，确保可以查询字段数据
        existing_fields = {f.name for f in collection.schema.fields}  # 获取 collection 当前实际存在的字段名
        kw_output = ["text", "source_file"]  # BM25 至少需要文本内容和来源文件
        if "keywords" in existing_fields:  # 如果 schema 中存在 keywords 字段
            kw_output.append("keywords")  # 查询时带上 keywords，提高 BM25 文本覆盖
        if "chunk_index" in existing_fields:  # 如果 schema 中存在 chunk_index 字段
            kw_output.append("chunk_index")  # 查询时带上 chunk_index，方便结果定位
        rows = collection.query(expr="", output_fields=kw_output, limit=2000)  # 拉取最多 2000 条文本用于内存 BM25
        if not rows:  # 如果 collection 中没有可查询文本
            return {}  # 返回空缓存
        doc_tokens_list = []  # 保存每篇文档的分词结果
        for row in rows:  # 遍历 Milvus 返回的每条 chunk
            text = str(row.get("text", ""))  # 读取 chunk 原文
            keywords = str(row.get("keywords", ""))  # 读取入库时提取的关键词
            # 将入库时提取的 keywords 和原文 text 合并，提高关键词召回覆盖率。
            haystack = f"{keywords} {text}".lower()  # 将关键词和正文合并为 BM25 的匹配文本，并统一小写
            doc_toks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", haystack)  # 用正则抽取中文、英文、数字 token
            doc_tokens_list.append(doc_toks)  # 保存当前文档 token 列表
        avgdl = sum(len(dt) for dt in doc_tokens_list) / max(len(rows), 1)  # 计算平均文档长度，BM25 归一化会用到
        cache_entry = {"rows": rows, "doc_tokens": doc_tokens_list, "avgdl": avgdl, "ts": _time.time()}  # 组装缓存数据
        _bm25_cache[character_id] = cache_entry  # 写入全局 BM25 缓存
        return cache_entry  # 返回新构建的缓存

    def search_keyword(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """BM25 全文检索：从缓存中获取文档后用 BM25 算法计算相关性。
        BM25 参数: k1=1.2, b=0.75（经典 Okapi BM25 设置）
        使用文档缓存避免重复拉取 Milvus。
        选择 BM25 的原因：
        - 对公司名、人名、年份、金额、股权比例等“精确词面匹配”非常敏感；
        - 相比纯向量检索，BM25 不容易把相似但不包含关键数字的片段排到前面；
        - 作为向量检索的互补通道，能显著提升财报/招股书问答中的数字类问题召回。
        """
        import math  # BM25 中需要使用 log 计算 IDF
        if top_k is None:  # 如果调用方没有指定返回数量
            top_k = settings.retrieval_top_k  # 使用配置中的默认召回数量
        tokens = self._tokenize_query(query)  # 对用户问题分词，得到 BM25 查询词
        if not tokens:  # 如果问题没有有效查询词
            return []  # 返回空结果
        # 优先使用缓存，避免每次问题都从 Milvus 拉取全量文本。
        cache = self._get_bm25_cache(character_id)  # 获取当前角色的 BM25 文档缓存
        if not cache:  # 如果缓存为空，说明没有可检索文本
            return []  # 返回空列表
        rows = cache["rows"]  # Milvus 原始 row 列表
        doc_tokens_list = cache["doc_tokens"]  # 每个 row 对应的 token 列表
        avgdl = cache["avgdl"]  # 平均文档长度

        # ---- BM25 参数 ----
        k1, b = 1.2, 0.75  # k1 控制词频饱和，b 控制文档长度归一化强度
        N = len(rows)  # 文档总数，用于计算 IDF

        # 统计每个查询词的文档频率 df
        df: dict[str, int] = {}  # 保存每个查询词出现在多少篇文档中
        for token in tokens:  # 遍历查询词
            tl = token.lower()  # 查询词统一小写，和文档 token 保持一致
            cnt = sum(1 for dt in doc_tokens_list if tl in " ".join(dt))  # 统计包含该查询词的文档数
            df[tl] = cnt  # 写入文档频率表

        # 计算每篇文档的 BM25 得分
        scored: list[dict[str, object]] = []  # 保存带 BM25 分数的候选结果
        for idx, row in enumerate(rows):  # 遍历每个文档 chunk
            doc_toks = doc_tokens_list[idx]  # 取出当前文档的 token 列表
            dl = len(doc_toks)  # 当前文档长度
            doc_text = " ".join(doc_toks)  # 将 token 拼回字符串，便于 count 统计词频
            score = 0.0  # 初始化当前文档 BM25 得分
            for token in tokens:  # 遍历所有查询词并累加得分
                tl = token.lower()  # 查询词统一小写
                tf = doc_text.count(tl)  # 统计查询词在当前文档中的出现次数
                if tf == 0:  # 当前文档没有该查询词
                    continue  # 跳过该词
                n_q = df.get(tl, 0)  # 获取该查询词的文档频率
                idf = math.log((N - n_q + 0.5) / (n_q + 0.5) + 1.0)  # 计算平滑后的 IDF
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avgdl, 1)))  # 计算 BM25 词频归一化项
                score += idf * tf_norm  # 累加当前查询词贡献
            if score <= 0:  # 如果所有查询词都没有贡献
                continue  # 不加入候选
            scored.append({
                "text": str(row.get("text", "")),
                "score": score,
                "source_file": row.get("source_file", ""),
                "chunk_index": row.get("chunk_index", 0),
                "keywords": str(row.get("keywords", "")),
                "method": "keyword_bm25",
            })
        scored.sort(key=lambda x: float(x["score"]), reverse=True)  # 按 BM25 分数从高到低排序
        return scored[:top_k]  # 返回前 top_k 条关键词召回结果

    def search_vector(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """向量检索：从Milvus中搜索语义最相关的文本片段（每个角色独立集合）。
        选择向量检索的原因：
        - 可以处理用户问题和原文表述不完全一致的情况，例如“主营业务”与“主要从事”；
        - 相比只用关键词，向量能理解同义表达、上下位概念和自然语言问句；
        - 每个角色独立 collection，避免不同角色知识互相污染，也便于按角色删除/重建知识库。
        这里使用 COSINE，是因为 embedding 语义相似度通常更关注方向而不是向量长度。
        """
        from pymilvus import Collection, connections, utility  # 导入 Milvus 连接、集合和工具函数
        if top_k is None:  # 如果调用方没有指定召回数量
            top_k = settings.retrieval_top_k  # 使用配置中的默认召回数量
        try:  # Milvus 连接可能失败，因此用 try 包裹
            # 连接 Milvus 后只检索当前角色对应的独立 collection，避免跨角色知识污染。
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)  # 建立 Milvus 连接
        except Exception:  # 连接失败时不能执行向量检索
            return []  # 返回空结果，保证 RAG 主流程可继续
        coll_name = self._collection_for(character_id)  # 生成当前角色对应的 collection 名
        if not utility.has_collection(coll_name):  # 如果当前角色没有向量库
            return []  # 返回空结果
        collection = Collection(coll_name)  # 获取 Milvus collection 对象
        collection.load()  # 将 collection 加载到内存，准备执行 ANN 检索
        # 查询问题也需要使用同一个 embedding 模型转成向量，才能与入库 chunk 向量比较相似度。
        query_vector = self._embed(query)  # 将用户问题转换为 embedding 向量
        results = collection.search(
            data=[query_vector],  # Milvus search 接收二维数组，这里只有一个查询向量
            anns_field="vector",  # 指定用于 ANN 检索的向量字段名
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},  # 使用 COSINE 相似度，nprobe 控制 IVF 搜索范围
            limit=top_k,  # 限制返回 top_k 条候选
            output_fields=[f.name for f in collection.schema.fields if f.name in ("text", "source_file", "chunk_index", "keywords")],  # 只取回答和引用需要的字段
        )  # 执行 Milvus 向量搜索
        rows: list[dict[str, object]] = []  # 保存转换后的检索结果
        # 将 Milvus hit 统一转成系统内部 row 格式，便于和 BM25 / Neo4j 结果合并。
        for hits in results:  # results 外层对应每个查询向量的命中列表
            for rank, hit in enumerate(hits, start=1):  # 遍历当前查询向量的命中结果，并从 1 开始编号
                rows.append({
                    "text": hit.entity.get("text", ""),  # 命中的文本 chunk
                    "score": float(getattr(hit, "distance", 0.0)),  # Milvus 返回的相似度/距离分数
                    "rank": rank,  # 当前命中在向量检索结果中的排名
                    "source_file": hit.entity.get("source_file", ""),  # 来源文件名
                    "chunk_index": hit.entity.get("chunk_index", 0),  # chunk 在原文中的序号
                    "keywords": hit.entity.get("keywords", ""),  # 入库时保存的关键词
                    "method": "vector",  # 标记该结果来自向量召回
                })  # 添加一条标准 row 结果
        return rows  # 返回向量召回结果列表

    def search_hybrid(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """混合检索：并行执行关键词、向量和 Neo4j 图谱检索，合并后按融合分数排序。
        完整流程：
        用户问题
          ↓
        Query Rewrite（可选，在 ChatService 中完成）
          ↓
        同时执行三路检索
          ├─ BM25 关键词检索：找包含关键词/数字/人名的 chunk
            入库时保存 keywords 字段，查询时从 Milvus 拉取文本和关键词，在 Python 内存里临时构建 BM25 文档缓存并计算分数。
          ├─ 向量检索 ANN：找语义相似的 chunk
          └─ Neo4j 图谱检索：找实体之间的结构化关系
          ↓
        三路结果合并去重
          ↓
        按 0.4（BM25）+ 0.6（向量）加权融合
          ↓
        取候选结果
          ↓
        Rerank 精排
          ↓
        返回 Top-K 给大模型作为上下文

        为什么采用 BM25 + 向量 + Neo4j 混合，而不是只用一种检索：
        - 纯 BM25：精确数字、人名召回好，但对同义改写、口语化问题不够鲁棒；
        - 纯向量：语义泛化强，但在财务数据、日期、股权比例等精确信息上可能“语义相近但事实不准”；
        - Neo4j 图谱检索补充实体关系、股权关系、关联方关系等结构化信息；
        - 混合检索把三者合并，既能抓住关键词，又能覆盖语义表达差异和结构化关系。
        权重默认向量 0.6、关键词 0.4：
        - 角色问答多数是自然语言问题，向量应占主导；
        - 招股说明书又包含大量专有名词和数字，保留 0.4 BM25 可以提高事实类问题稳定性。
        Neo4j 在这里的作用是：不参与 0.6/0.4 的“向量 + 关键词”加权公式，但会作为第三路结构化召回，额外把图谱关系片段加入候选集，
        并影响最终排序，补充结构化关系，如果 Neo4j 返回的文本和已有 BM25/向量结果重复说明这个结果同时被图谱命中，更可信
        """
        # 导入 ThreadPoolExecutor，用于创建线程池，实现并行执行任务（提高检索效率）
        from concurrent.futures import ThreadPoolExecutor  # 导入线程池，用于并发执行多路召回
        # 如果没有指定 top_k（返回的结果数量），则使用配置文件中的默认值
        if top_k is None:
            top_k = settings.retrieval_top_k
        # 导入 Neo4jGraphService（图数据库服务类），用于从 Neo4j 中检索关系数据
        from app.services.neo4j_graph_service import Neo4jGraphService  # 导入 Neo4j 图谱召回服务
        # 创建一个最大工作线程数为 3 的线程池（同时执行 3 个检索任务）
        with ThreadPoolExecutor(max_workers=3) as pool:  # 创建 3 个工作线程，分别跑 BM25、向量、Neo4j
            # 提交关键词检索任务到线程池，返回一个 Future 对象
            # self.search_keyword: BM25 关键词检索方法
            # 参数：character_id 表示角色 ID，query 表示检索问题，max(top_k, 8) 表示至少召回 8 条候选
            kw_future = pool.submit(self.search_keyword, character_id, query, max(top_k, 8))  # 异步提交 BM25 关键词召回
            # 提交向量检索任务到线程池（语义相似度检索）
            # self.search_vector: 向量检索方法（Embedding + Milvus）
            vec_future = pool.submit(self.search_vector, character_id, query, max(top_k, 8))  # 异步提交 Milvus 向量召回
            # 提交 Neo4j 图数据库检索任务
            # Neo4jGraphService().search_rows: 从 Neo4j 知识图谱中检索角色相关的实体关系，失败时返回空列表
            # neo4j_top_k: 从配置中读取，控制 Neo4j 最多返回的关系条数
            neo4j_future = pool.submit(Neo4jGraphService().search_rows, character_id, query,max(top_k, settings.neo4j_top_k))  # 异步提交 Neo4j 图谱关系召回
            # 统一获取三路召回结果；result() 会等待对应任务完成
            kw_rows = self._safe_retrieval_result(kw_future, "keyword")  # 等待 BM25 召回完成并取回结果
            vec_rows = self._safe_retrieval_result(vec_future, "vector")  # 等待向量召回完成并取回结果
            neo4j_rows = self._safe_retrieval_result(neo4j_future, "neo4j")  # 等待 Neo4j 召回完成并取回结果
        # 用 max(top_k, 8) 扩大召回量：多路各取足够候选，为后续融合和 rerank 留出冗余。
        # 并行执行：BM25、向量检索和 Neo4j 图谱检索互不依赖，可降低整体等待时间。

        logger.info("[Hybrid] query=%s, keyword_hits=%d, vector_hits=%d, neo4j_hits=%d", query[:60], len(kw_rows), len(vec_rows), len(neo4j_rows))  # 记录三路召回命中数量
        for i, r in enumerate(kw_rows[:5], 1):  # 只打印前 5 条 BM25 结果，避免 debug 日志过长
            logger.debug("  [BM25  %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])  # 输出 BM25 候选摘要
        for i, r in enumerate(vec_rows[:5], 1):  # 只打印前 5 条向量结果
            logger.debug("  [ANN   %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])  # 输出向量候选摘要
        for i, r in enumerate(neo4j_rows[:5], 1):  # 只打印前 5 条 Neo4j 结果
            logger.debug("  [Neo4j %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])  # 输出图谱候选摘要

        merged: dict[str, dict[str, object]] = {}  # 以 text 为 key 做去重和多路分数融合

        # 第一阶段：写入 BM25 结果，关键词分数按 hybrid_keyword_weight 加权。
        for row in kw_rows:  # 遍历 BM25 返回的候选
            text = str(row.get("text", ""))  # 取出候选文本，作为去重 key
            if not text:  # 如果候选没有文本内容
                continue  # 跳过无效候选
            merged[text] = {  # 将 BM25 候选写入融合字典
                **row,  # 保留原始 row 字段，如 source_file、chunk_index、keywords 等
                "vector_score": 0.0,  # BM25 候选初始没有向量分数
                "keyword_score": float(row.get("score", 0.0)),  # 保存原始 BM25 分数
                "hybrid_score": float(row.get("score", 0.0)) * settings.hybrid_keyword_weight,  # 按关键词权重计算融合分
            }  # 完成 BM25 候选写入

        vec_scores = [float(r.get("score", 0.0)) for r in vec_rows]  # 收集向量召回原始分数
        min_vec = min(vec_scores) if vec_scores else 0.0  # 计算向量分数最小值，用于归一化
        max_vec = max(vec_scores) if vec_scores else 0.0  # 计算向量分数最大值，用于归一化

        # 第二阶段：合并向量结果。若文本已由 BM25 命中，则累加向量分数并标记为 hybrid。
        for row in vec_rows:  # 遍历向量召回候选
            text = str(row.get("text", ""))  # 取出候选文本，用于和 BM25 结果去重合并
            if not text:  # 如果文本为空
                continue  # 跳过无效候选
            normalized_vec = self._normalize_score(float(row.get("score", 0.0)), min_vec, max_vec)  # 将向量原始分数归一化到 0~1
            existing = merged.get(text)  # 检查该文本是否已经被 BM25 召回
            if existing:  # 如果同一文本已存在
                existing["vector_score"] = normalized_vec  # 补充向量分数
                existing["hybrid_score"] = float(existing.get("hybrid_score", 0.0)) + normalized_vec * settings.hybrid_vector_weight  # 累加向量加权分
                existing["method"] = "hybrid"  # 标记为 BM25 + 向量同时命中
            else:
                merged[text] = {  # 如果 BM25 没命中，则新增向量候选
                    **row,  # 保留向量结果原始元数据
                    "vector_score": normalized_vec,  # 保存归一化后的向量分数
                    "keyword_score": 0.0,  # 该候选没有关键词分数
                    "hybrid_score": normalized_vec * settings.hybrid_vector_weight,  # 按向量权重计算融合分
                    "method": "vector",  # 标记为纯向量召回
                }  # 完成向量候选写入

        # 第三阶段：合并 Neo4j 图谱关系，将结构化关系作为候选片段参与 rerank。
        for row in neo4j_rows:  # 遍历 Neo4j 图谱召回候选
            text = str(row.get("text", ""))  # 取出图谱关系文本
            if not text:  # 如果图谱候选文本为空
                continue  # 跳过无效候选
            existing = merged.get(text)  # 检查图谱候选是否与已有文本完全相同
            if existing:  # 如果已经存在相同文本
                existing["graph_score"] = 1.0  # 添加图谱分数
                existing["hybrid_score"] = float(existing.get("hybrid_score", 0.0)) + 1.0  # 图谱命中直接增加融合分
                existing["method"] = "hybrid_graph"  # 标记为文本召回与图谱召回共同命中
            else:
                merged[text] = {  # 如果是新的图谱关系，则作为独立候选加入
                    **row,  # 保留 Neo4j row 中的 source_file、chunk_index、keywords 等字段
                    "vector_score": 0.0,  # 图谱候选没有向量分数
                    "keyword_score": 0.0,  # 图谱候选没有 BM25 分数
                    "graph_score": 1.0,  # 图谱关系候选使用固定结构化分数
                    "hybrid_score": 1.0,  # 图谱候选的初始融合分
                    "method": "neo4j_graph",  # 标记来源为 Neo4j 图谱召回
                }  # 完成图谱候选写入

        # 按融合分数取候选集，再交给 rerank 模型做最终相关性排序。
        # 注意：Neo4j 图谱结果的 hybrid_score 是固定分，容易在融合排序阶段排在向量结果前面。
        # 因此这里扩大进入 rerank 的候选池，避免 Milvus 向量片段还没进入精排就被 Neo4j 挤掉。
        final_rows = sorted(merged.values(), key=lambda x: float(x.get("hybrid_score", 0.0)), reverse=True)  # 按融合分从高到低排序
        candidate_limit = max(top_k * 2, settings.rerank_top_k * 2, top_k + 4)  # 候选池收紧：减少 reranker 推理对数，降低 CPU 耗时
        candidates = final_rows[:candidate_limit]  # 取足够多候选交给 rerank，避免过早截断
        logger.info("[Hybrid] merged=%d, candidates=%d (for rerank)", len(final_rows), len(candidates))  # 记录融合后和精排前候选数量
        for i, r in enumerate(candidates, 1):  # 遍历候选用于 debug 输出
            logger.debug("  [Fused %d] hybrid=%.4f method=%-8s text=%s...", i, float(r.get("hybrid_score", 0)), r.get("method", ""), str(r.get("text", ""))[:80])  # 输出融合候选摘要
        return self._rerank(query, candidates, top_n=top_k)  # 调用 rerank 精排并返回最终结果

    @staticmethod
    def _safe_retrieval_result(future, name: str) -> list[dict[str, object]]:
        try:
            rows = future.result()
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            logger.warning("[Hybrid] %s retrieval failed: %s", name, exc, exc_info=True)
            return []

    def _rerank(self, query: str, rows: list[dict[str, object]], top_n: int | None = None) -> list[dict[str, object]]:
        """优先使用本地 bge-reranker-v2-m3 精排；本地不可用时回退到 SiliconFlow API；两者失败则回退融合分。"""
        if not settings.rerank_enabled or not rows:
            return rows[:top_n] if top_n else rows
        if top_n is None:
            top_n = settings.rerank_top_k

        valid_indices: list[int] = []
        documents: list[str] = []
        for i, row in enumerate(rows):
            text = str(row.get("text", "")).strip()
            if text:
                valid_indices.append(i)
                documents.append(text)

        if not documents:
            return rows[:top_n]

        return self._rerank_api(query, rows, valid_indices, documents, top_n)

    @staticmethod
    def _rerank_local(query: str, rows: list[dict[str, object]], valid_indices: list[int], documents: list[str], top_n: int, model_path: str) -> list[dict[str, object]]:
        """使用本地 CrossEncoder（bge-reranker-v2-m3）对候选文档精排。
        模型以单例形式缓存，首次调用时加载，后续直接复用，避免重复加载权重。
        """
        global _local_reranker, _local_reranker_path
        if _local_reranker is None or _local_reranker_path != model_path:  # 首次或路径变更时重新加载
            from sentence_transformers import CrossEncoder
            logger.info("[Rerank] 加载本地模型: %s", model_path)
            _local_reranker = CrossEncoder(model_path, max_length=512, local_files_only=True)  # 强制本地加载，不联网
            _local_reranker_path = model_path
            logger.info("[Rerank] 本地模型加载完成")
        pairs = [(query, doc) for doc in documents]  # 构造 (问题, 文档) 配对输入
        scores = _local_reranker.predict(pairs)  # 批量推理，返回每对的相关性得分
        scored: list[dict[str, object]] = []
        for doc_i, score in zip(valid_indices, scores):  # 把分数写回原始 row
            row = dict(rows[doc_i])
            row["rerank_score"] = float(score)
            row["rerank_used"] = True
            scored.append(row)
        scored.sort(key=lambda x: float(x["rerank_score"]), reverse=True)  # 按相关性从高到低排序
        result = scored[:top_n]
        logger.info("[Rerank] 本地精排完成: input=%d, output=%d", len(documents), len(result))
        for i, r in enumerate(result, 1):
            logger.info("  [Rerank %d] score=%.4f text=%s...", i, float(r.get("rerank_score", 0)), str(r.get("text", ""))[:80])
        return result

    @staticmethod
    def _rerank_api(query: str, rows: list[dict[str, object]], valid_indices: list[int], documents: list[str], top_n: int) -> list[dict[str, object]]:
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if not base_url or not api_key:
            logger.warning("Rerank skipped: missing API base or key")
            return rows[:top_n]
        try:
            import httpx
            url = f"{base_url}/rerank"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": settings.rerank_model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
            }
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            results = data.get("results", [])
            reranked: list[dict[str, object]] = []
            for r in results:
                doc_idx = r.get("index", 0)
                if 0 <= doc_idx < len(valid_indices):
                    orig_idx = valid_indices[doc_idx]
                    row = dict(rows[orig_idx])
                    row["rerank_score"] = r.get("relevance_score", 0.0)
                    row["rerank_used"] = True
                    reranked.append(row)
            logger.info("[Rerank] API done: input=%d, output=%d, model=%s", len(documents), len(reranked), settings.rerank_model)
            return reranked
        except Exception as e:
            logger.warning("Rerank API failed, fallback to hybrid score: %s", e)
            return rows[:top_n]

    @staticmethod
    def _prepare_embedding_input(text: str, max_chars: int) -> str:
        """清理并截断 embedding 输入，减少控制字符和超长文本导致的 API 失败。"""
        prepared = re.sub(r"\s+", " ", text).strip()  # 压缩连续空白，减少无意义 token
        prepared = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", prepared)  # 移除控制字符，避免 JSON/API 解析异常
        return prepared[:max_chars]  # 截断到指定最大字符数

    def _embed(self, text: str, *, use_cache: bool = True) -> list[float]:
        """将文本转换为向量：优先查缓存 → 本地模型 → 调用Embedding API → 退化为SHA256伪向量"""
        embed_text = self._prepare_embedding_input(text, 1000)  # 使用 1000 字符版本作为缓存 key 的基础文本
        cache_key = hashlib.md5(embed_text.encode("utf-8")).hexdigest()  # 生成短哈希作为缓存键
        # 相同文本片段重复检索时直接复用缓存，减少模型调用次数。
        if use_cache and cache_key in _embed_cache:
            _embed_cache.move_to_end(cache_key)  # 刷新 LRU 顺序
            return _embed_cache[cache_key]
        # 优先使用本地 bge-m3 模型，不依赖外部 API，速度稳定。
        local_path = (settings.embedding_local_model_path or "").strip()
        if local_path:
            vec = self._embed_local(embed_text, local_path)
            if vec:
                self._cache_embed_result(cache_key, vec)
                return vec
        base_url = (settings.openai_api_base or "").rstrip("/")  # 读取 embedding API 基础地址
        api_key = settings.openai_api_key or ""  # 读取 API Key
        if base_url and api_key:  # 只有配置齐全时才调用外部 embedding
            import httpx  # 导入 HTTP 客户端
            url = f"{base_url}/embeddings"  # 构造 embedding 接口地址
            headers = {"Authorization": f"Bearer {api_key}"}  # 设置鉴权头
            try:  # embedding 调用失败时需要回退到伪向量
                with httpx.Client(timeout=15.0, trust_env=False) as client:  # 创建 HTTP 客户端并设置超时
                    last_error: Exception | None = None  # 保存最后一次请求异常，便于重试全部失败后抛出
                    # 逐级缩短输入重试，优先保留更多上下文；遇到 413 时再降到 600/300 字符。
                    for max_chars in (1000, 600, 300):  # 按 1000、600、300 字符逐级缩短重试
                        candidate = self._prepare_embedding_input(text, max_chars)  # 生成当前长度限制下的输入文本
                        if not candidate:  # 如果清洗后为空
                            continue  # 跳过当前候选
                        payload = {"model": settings.embedding_model_name, "input": candidate}  # 构造 embedding 请求体
                        try:  # 单次 embedding 请求
                            resp = client.post(url, headers=headers, json=payload)  # 发送 embedding 请求
                            resp.raise_for_status()  # 非成功状态码抛出异常
                            data = resp.json()  # 解析 JSON 响应
                            vec = data["data"][0]["embedding"][:settings.milvus_dim]  # 截取符合 Milvus 维度的向量
                            self._cache_embed_result(cache_key, vec)  # 缓存 embedding 结果
                            return vec  # 返回真实 embedding 向量
                        except httpx.HTTPStatusError as exc:  # 捕获 HTTP 状态码异常
                            last_error = exc  # 保存异常，便于所有重试失败后处理
                            if exc.response.status_code != 413:  # 只有 413 表示输入过大，其他错误不继续缩短重试
                                raise  # 重新抛出非 413 错误
                    if last_error:  # 如果所有长度都失败且记录了异常
                        raise last_error  # 抛出最后一次异常，进入外层 fallback
            except Exception as e:  # embedding 请求整体失败时进入伪向量回退
                logger.warning("Embedding API failed, falling back to SHA256: %s", e)  # 记录失败原因，方便排查 API/网络问题
        # 外部 embedding 不可用时生成固定维度伪向量，保证入库/检索流程不中断。
        digest = hashlib.sha256(text.encode("utf-8")).digest()  # 对原始文本计算 SHA256 摘要，作为伪向量来源
        vector: list[float] = []  # 保存生成的伪向量
        for i in range(settings.milvus_dim):  # 按配置维度生成每一维
            byte = digest[i % len(digest)]  # 循环使用 SHA256 摘要中的字节
            vector.append((byte / 255.0) * 2 - 1)  # 将字节值映射到 [-1, 1] 区间
        return vector  # 返回固定维度伪向量

    @staticmethod
    def _embed_local(text: str, model_path: str) -> list[float]:
        """使用本地 bge-m3 生成 dense 向量。
        优先走 onnx/ 子目录的 ONNX 模型（不依赖 PyTorch 版本）；
        ONNX 不可用时尝试 SentenceTransformer（需要 safetensors 或 torch>=2.6）。
        单例复用，避免重复加载权重。
        """
        global _local_embedder, _local_embedder_path
        import numpy as np
        from pathlib import Path as _Path

        onnx_dir  = _Path(model_path) / "onnx"
        onnx_file = onnx_dir / "model.onnx"

        # ===== ONNX 路径：不依赖 PyTorch，安全尔快 =====
        if onnx_file.exists():
            try:
                # 单例缓存 key 用 onnx_dir 路径，与 SentenceTransformer 单例共用 slot
                onnx_key = str(onnx_file)
                if _local_embedder is None or _local_embedder_path != onnx_key:
                    import onnxruntime as ort
                    from tokenizers import Tokenizer
                    logger.info("[Embed] 加载 ONNX Embedding 模型: %s", onnx_file)
                    sess_opts = ort.SessionOptions()
                    sess_opts.intra_op_num_threads = 4
                    # 关闭 arena 分配器，改用 OS 原生 allocator。
                    # bge-m3 ONNX 权重文件 ~570MB，服务器运行时内存端片天起导致 arena bad allocation。
                    sess_opts.enable_cpu_mem_arena = False
                    sess_opts.enable_mem_pattern = False
                    sess = ort.InferenceSession(
                        str(onnx_file),
                        sess_options=sess_opts,
                        providers=["CPUExecutionProvider"],
                    )
                    tok  = Tokenizer.from_file(str(onnx_dir / "tokenizer.json"))
                    tok.enable_truncation(max_length=512)
                    tok.enable_padding()
                    _local_embedder      = (sess, tok)  # 将 session 和 tokenizer 打包存入单例
                    _local_embedder_path = onnx_key
                    logger.info("[Embed] ONNX 模型加载完成")

                sess, tok = _local_embedder  # type: ignore[misc]
                enc  = tok.encode(text)
                ids  = np.array([enc.ids],              dtype=np.int64)
                mask = np.array([enc.attention_mask],   dtype=np.int64)
                tids = np.zeros_like(ids)               # token_type_ids 全 0
                input_names = {inp.name for inp in sess.get_inputs()}
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in input_names:
                    feed["token_type_ids"] = tids
                out     = sess.run(None, feed)           # out[0]: (1, seq_len, hidden)
                hidden  = out[0][0]                      # (seq_len, hidden)
                # mean pooling （忽略 padding 位）
                m       = np.array(enc.attention_mask, dtype=np.float32)[:, None]
                pooled  = (hidden * m).sum(axis=0) / (m.sum() + 1e-9)  # (hidden,)
                norm    = np.linalg.norm(pooled)
                if norm > 0:
                    pooled = pooled / norm
                return pooled.tolist()[:settings.milvus_dim]
            except Exception as exc:
                logger.warning("[Embed] ONNX 失败，尝试 SentenceTransformer: %r", exc)
                _local_embedder      = None   # 重置单例，下次再试 ST
                _local_embedder_path = ""

        # ===== 回退： SentenceTransformer（需要 safetensors 或 torch>=2.6） =====
        try:
            if _local_embedder is None or _local_embedder_path != model_path:
                from sentence_transformers import SentenceTransformer
                logger.info("[Embed] 加载本地 SentenceTransformer: %s", model_path)
                _local_embedder      = SentenceTransformer(model_path, trust_remote_code=True)
                _local_embedder_path = model_path
                logger.info("[Embed] SentenceTransformer 加载完成")
            vec = _local_embedder.encode(text, normalize_embeddings=True).tolist()  # type: ignore[union-attr]
            return vec[:settings.milvus_dim]
        except Exception as exc:
            logger.warning("[Embed] 本地模型全部失败，回退到 API: %r", exc)
            return []

    def _cache_embed_result(self, cache_key: str, vec: list[float]) -> None:
        """将 embedding 结果存入 LRU 缓存"""
        _embed_cache[cache_key] = vec  # 写入或更新缓存
        if len(_embed_cache) > _EMBED_CACHE_MAX:  # 如果缓存超过容量上限
            _embed_cache.popitem(last=False)  # 弹出最早插入/最久未使用的项，实现 LRU 淘汰

    def _insert_into_milvus(self, rows: list[dict[str, object]], character_id: int) -> None:
        """将向量数据批量写入Milvus（每个角色独立集合，如果集合不存在或维度不匹配会自动创建/重建）"""
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility  # 导入 Milvus schema、字段、集合和工具类

        connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)  # 连接 Milvus 服务
        coll_name = self._collection_for(character_id)  # 根据角色 ID 获取 collection 名
        need_create = False  # 标记是否需要创建或重建 collection
        # 如果 collection 已存在但向量维度与当前 embedding 模型不一致，必须重建，否则 Milvus 无法插入。
        if utility.has_collection(coll_name):  # 如果 collection 已经存在
            existing = Collection(coll_name)  # 获取已有 collection
            for f in existing.schema.fields:  # 遍历已有 schema 字段
                if f.name == "vector" and f.params.get("dim") != settings.milvus_dim:  # 如果向量字段维度和当前配置不一致
                    utility.drop_collection(coll_name)  # 删除旧 collection，避免维度不匹配导致插入失败
                    need_create = True  # 标记需要重新创建 collection
                    break  # 找到维度问题后退出字段遍历
        else:
            need_create = True  # collection 不存在时需要新建
        if need_create:  # 需要创建 collection 时定义 schema
            # 定义 Milvus schema：文本字段用于展示和 BM25，vector 字段用于 ANN 检索。
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),  # Milvus 自动生成的主键ID
                FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=255),  # 来源文件名，用于展示引用出处
                FieldSchema(name="chunk_index", dtype=DataType.INT64),  # 文本块序号，用于定位原文位置
                FieldSchema(name="chunk_hash", dtype=DataType.VARCHAR, max_length=64),  # 文本 SHA256 指纹，用于去重
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),  # 原始文本块，作为 RAG 上下文返回
                FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=4096),  # 关键词串，用于 BM25 关键词检索
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=settings.milvus_dim),  # 语义向量字段，用于 ANN 相似度检索
            ]
            schema = CollectionSchema(fields, description=f"Knowledge base for character {character_id}")  # 创建 collection schema
            collection = Collection(name=coll_name, schema=schema)  # 按 schema 创建新的 Milvus collection
        else:
            collection = Collection(coll_name)  # 复用已有 collection
        if need_create:  # 只有新建 collection 时需要创建索引
            # 使用 COSINE 度量与检索阶段保持一致；IVF_FLAT 是较简单稳定的近似检索索引。
            index_params = {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}}  # 定义向量索引参数---基于倒排索引的近似最近邻（ANN）索引

            # 优点：建索引快、召回率高、无精度损失（相比量化类索引如 IVF_PQ）。
            # 缺点：搜索时计算量较大（需遍历 nprobe 个簇内的所有向量），内存占用较高（因存储原始向量）。

            collection.create_index(field_name="vector", index_params=index_params)  # 为 vector 字段创建索引
        collection.load()  # 加载 collection，确保后续插入和查询可用
        # Milvus insert 使用按字段组织的列式数据，因此需要把 rows 转成多列列表。
        columns = [
            [row["source_file"] for row in rows],  # source_file 列：每个 chunk 的来源 PDF
            [row.get("chunk_index", 0) for row in rows],  # chunk_index 列：每个 chunk 的顺序编号
            [row["chunk_hash"] for row in rows],  # chunk_hash 列：每个 chunk 的去重指纹
            [row["text"] for row in rows],  # text 列：实际可被检索和送入 prompt 的文本
            [row.get("keywords", "") for row in rows],  # keywords 列：BM25 使用的关键词补充
            [row["vector"] for row in rows],  # vector 列：Milvus 建索引和向量搜索使用
        ]
        collection.insert(columns)  # 批量插入列式数据
        collection.flush()  # 强制刷盘，确保数据持久化并可被后续查询看到
