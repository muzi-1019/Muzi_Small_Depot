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

logger = logging.getLogger(__name__)

# ========== Embedding 查询缓存（LRU，最多缓存 512 条） ==========
_EMBED_CACHE_MAX = 512
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()

# ========== BM25 文档缓存（避免每次重新拉取 Milvus） ==========
_bm25_cache: dict[int, dict] = {}  # character_id -> {rows, doc_tokens, avgdl, ts}

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
        return f"{settings.milvus_collection}_{character_id}"

    def ingest_all(self) -> dict[str, int]:
        """批量入库：根据预定义的角色-PDF映射关系，将所有PDF文件解析并写入向量库"""
        mapping = self._role_pdf_mapping()
        inserted = 0
        scanned = 0
        for character_id, pdf_path in mapping.items():
            scanned += 1
            inserted += self.ingest_file(character_id, pdf_path)
        return {"scanned": scanned, "inserted": inserted}

    def ingest_file(self, character_id: int, pdf_path: Path) -> int:
        """单文件入库：解析指定PDF → 切分文本 → 向量化 → 写入Milvus，返回写入的向量条数"""
        if not pdf_path.exists():
            return 0
        logger.info("[PDF ingest] start character_id=%s file=%s", character_id, pdf_path)
        text = self._extract_text(pdf_path)
        chunks = self._chunk_text(text)
        logger.info("[PDF ingest] extracted chars=%d chunks=%d file=%s", len(text), len(chunks), pdf_path.name)
        if not chunks:
            return 0
        rows = []
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index % 20 == 0:
                logger.info("[PDF ingest] embedding progress %d/%d file=%s", chunk_index, len(chunks), pdf_path.name)
            rows.append(self._build_row(character_id, pdf_path, chunk, chunk_index))
        logger.info("[PDF ingest] inserting rows=%d character_id=%s", len(rows), character_id)
        self._insert_into_milvus(rows, character_id)
        logger.info("[PDF ingest] done rows=%d character_id=%s file=%s", len(rows), character_id, pdf_path.name)
        return len(rows)

    def ingest_text(self, character_id: int, source_name: str, text: str) -> int:
        if not text.strip():
            return 0
        chunks = self._chunk_text(text)
        logger.info("[Text ingest] extracted chars=%d chunks=%d source=%s", len(text), len(chunks), source_name)
        if not chunks:
            return 0
        source_path = Path(source_name)
        rows = []
        # 每个rows包括'''{
        # "source_file": 文件名,
        # "chunk_index": 第几个片段,
        # "text": chunk 文本,
        # "keywords": 关键词,
        # "vector": embedding 向量,
        # "chunk_hash": 文本哈希
        # }'''
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index % 20 == 0:
                logger.info("[Text ingest] embedding progress %d/%d source=%s", chunk_index, len(chunks), source_name)
            rows.append(self._build_row(character_id, source_path, chunk, chunk_index))
        logger.info("[Text ingest] inserting rows=%d character_id=%s source=%s", len(rows), character_id, source_name)
        self._insert_into_milvus(rows, character_id)
        logger.info("[Text ingest] done rows=%d character_id=%s source=%s", len(rows), character_id, source_name)
        return len(rows)

    def _role_pdf_mapping(self) -> dict[int, Path]:
        """预定义的角色ID与PDF文件的映射关系（硬编码的初始知识库配置）"""
        data_dir = Path(settings.data_dir)
        return {
            2: data_dir / "data/国家基层高血压防治管理手册2025版.pdf",
            3: data_dir / "data/中华人民共和国宪法.pdf",
        }

    def _extract_text(self, pdf_path: Path) -> str:
        """从PDF文件中提取全部文字内容（优先用PyMuPDF，不可用时用pypdf，扫描件用OCR识别）。
        增强：自动检测表格并转为 Markdown 格式，保留表格结构信息用于精准检索。
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        doc = fitz.open(str(pdf_path))
        ocr_engine = None
        pages: list[str] = []
        total_pages = len(doc)
        for page_index, page in enumerate(doc, 1):
            logger.info("[PDF ingest] parsing page %d/%d file=%s", page_index, total_pages, pdf_path.name)
            table_md = self._extract_tables_as_markdown(page)
            text = page.get_text("text") or ""
            if len(text.strip()) < 30:
                if ocr_engine is None:
                    ocr_engine = self._get_ocr_engine()
                ocr_text = self._ocr_page(page, ocr_engine)
                text = ocr_text if ocr_text else text
            image_desc = self._extract_images_as_text(page)
            if table_md:
                text = text + "\n\n" + table_md
            if image_desc:
                text = text + "\n\n" + image_desc
            pages.append(text)
        doc.close()
        return "\n".join(pages)

    def _extract_images_as_text(self, page) -> str:
        """从PDF页面中提取图像，使用多模态视觉模型生成文字描述，使图像内容可被语义检索。
        仅处理面积较大的图像（>100x100像素），避免处理小图标和装饰元素。
        """
        try:
            import fitz
            images = page.get_images(full=True)
            if not images:
                return ""
        except Exception:
            return ""
        descriptions: list[str] = []
        doc = page.parent
        for img_index, img_info in enumerate(images[:3], 1):
            # 这里只处理每页前 3 张图片。
            # 原因是：
            # 避免一页里有太多小图导致调用视觉模型太慢
            # 控制成本
            # 避免入库时间过长
            try:
                xref = img_info[0]
                # xref 是 PDF 内部图片引用 ID
                # 通过它可以提取图片二进制数据
                base_image = doc.extract_image(xref)
                if not base_image:
                    continue
                width = base_image.get("width", 0)
                height = base_image.get("height", 0)
                if width < 100 or height < 100:
                    continue
                    # 小于 100x100 的图片跳过。
                    # 原因是很多 PDF 里有：logo、icon、装饰图、页眉页脚小图
                    # 这些对检索价值不大，直接返回 continue
                image_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                logger.info("[PDF ingest] describing image %d/%d size=%sx%s", img_index, min(len(images), 3), width, height)
                desc = self._describe_image(image_bytes, ext)
                if desc:
                    descriptions.append(f"[图像内容描述] {desc}")
            except Exception:
                continue
        return "\n".join(descriptions)

    @staticmethod
    def _describe_image(image_bytes: bytes, ext: str = "png") -> str:
        """调用配置中的视觉模型"deepseek-ai/DeepSeek-OCR"，为 PDF 中提取出的图片生成中文描述。
        图片会被编码为 base64 data URL，并通过 OpenAI 兼容的 chat/completions 接口发送。
        如果外部接口调用失败，则返回空字符串，不中断 PDF 入库流程。
        """
        import base64
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if not base_url or not api_key:
            return ""
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, f"image/{ext}")
        # 根据图片后缀生成 MIME 类型，因为互联网媒体类型（MIME）有严格标准，图片格式必须使用规范的类型名
        # PNG 标准是 image/png，JPEG 标准是 image/jpeg（不是 image/jpg）
        # 直接使用 f"image/{ext}" 会产生 image/jpg（非标准），可能导致某些浏览器或 API 解析异常
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        # HTTP/HTML/JSON 只能传输文本，不能直接传输二进制数据
        # Base64 将二进制（8-bit bytes）编码为 ASCII 文本（A-Z、a-z、0-9、+、/）---（二进制安全传输）
        # 传统方式：图片需要单独 HTTP 请求，浏览器要额外请求
        # Data URL 方式：图片数据作为 HTML/CSS 的一部分，一次请求完成，可以 ---（减少 HTTP 请求次数）
        # API 接口传输图片时，Base64 字符串可以放在 JSON 中 ---（便于数据交换）
        # .b64encode() → 编码为 bytes / .decode("utf-8") → 转为字符串
        # 接收方用 base64.b64decode(b64) 还原原始二进制 ---（编码和解码的可逆性）

        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": settings.vision_model_name,
            "messages": [
                {"role": "system", "content": "你是一个图像分析助手。请用中文简洁描述图像中的关键信息，包括但不限于图表类型、数据趋势、组织结构等。不超过200字。"},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "请描述这张图片的内容。"},
                ]},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }
        try:
            import httpx
            with httpx.Client(timeout=8.0, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("图像描述失败: %s", e)
            return ""

    @staticmethod
    def _extract_tables_as_markdown(page) -> str:
        """从PDF页面中检测并提取表格，转换为 Markdown 格式以保留结构信息。
        利用 PyMuPDF 的 find_tables() API 自动识别表格边界和单元格。
        """
        try:
            tabs = page.find_tables()
            if not tabs or not tabs.tables:
                return ""
        except Exception:
            return ""
        parts: list[str] = []
        for table in tabs.tables:
            try:
                data = table.extract()
                if not data or len(data) < 1:
                    continue
                headers = [str(cell or "").strip().replace("\n", " ") for cell in data[0]]
                if not any(headers):
                    continue
                md_lines = ["| " + " | ".join(headers) + " |"]
                md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in data[1:]:
                    cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
                    md_lines.append("| " + " | ".join(cells) + " |")
                parts.append("\n".join(md_lines))
            except Exception:
                continue
        return "\n\n".join(parts)

    @staticmethod
    def _get_ocr_engine():
        """获取OCR引擎实例（用于识别扫描版PDF中的图片文字）。
        这里选择 RapidOCR 而不是直接依赖云端 OCR：
        1. 可离线运行，不会把用户上传的 PDF 图片传到第三方 OCR 平台，隐私风险更低；
        2. 基于 ONNX Runtime，部署比 PaddleOCR 更轻量，Windows 本地环境更容易安装；
        3. 对中文扫描件、表格截图中的普通文本识别效果足够支撑 RAG 入库。
        如果未安装 rapidocr_onnxruntime，会自动跳过 OCR，不影响普通文字版 PDF 的解析流程。
        """
        try:
            from rapidocr_onnxruntime import RapidOCR
            return RapidOCR()
        except ImportError:
            return None

    @staticmethod
    def _ocr_page(page, ocr_engine) -> str | None:
        """对单个PDF页面进行OCR文字识别（当普通文本提取结果太少时使用）。
        只在页面文字少于阈值时触发 OCR，而不是每页都 OCR：
        - 普通 PDF 的文本层提取速度远快于 OCR，直接提取即可；
        - OCR 成本更高且可能产生识别误差，作为扫描件兜底更合适；
        - 2 倍缩放渲染能提升小字识别率，同时不会像 3~4 倍那样显著增加内存和耗时。
        """
        if ocr_engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                ocr_engine = RapidOCR()
            except ImportError:
                return None
        try:
            import fitz  # PyMuPDF
            from PIL import Image
            import io
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            result, _ = ocr_engine(img)
            if result:
                return "\n".join(line[1] for line in result)
        except Exception:
            pass
        return None

    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
        """将长文本切分成固定大小的片段，相邻片段之间有重叠区域（避免关键信息被切断）。
        选择 800 字符作为默认 chunk_size：
        - 比 300~500 字符更容易保留完整段落、表格行和上下文关系；
        - 比 1500+ 字符更不容易混入多个主题，向量表示更集中，检索命中更精准；
        - 对中文招股说明书这类长文档，800 字符在“语义完整性”和“检索粒度”之间较均衡。
        选择 120 字符 overlap：
        - 约 15% 重叠率，能防止股东名称、金额、比例等关键信息刚好被切在边界；
        - 相比 0 重叠召回更稳，相比 300+ 重叠又不会明显放大 Milvus 存储和检索成本。
        """
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(cleaned):
            end = min(len(cleaned), start + chunk_size)
            chunks.append(cleaned[start:end].strip())
            if end >= len(cleaned):
                break
            start = max(end - overlap, start + 1)
        return [c for c in chunks if c]

    def _build_row(self, character_id: int, pdf_path: Path, chunk_text: str, chunk_index: int = 0) -> dict[str, object]:
        """为单个文本片段构建完整的数据行（来源文件、文本、向量、哈希指纹，角色通过独立集合隔离）"""
        keywords = self._extract_keywords(chunk_text)
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
        mode = (mode or settings.retrieval_mode).lower()
        if mode == "vector":
            return self.search_vector(character_id, query, top_k)
        elif mode == "keyword":
            return self.search_keyword(character_id, query, top_k)
        else:
            return self.search_hybrid(character_id, query, top_k)

    def search(self, character_id: int, query: str, top_k: int | None = None) -> list[str]:
        """在Milvus中搜索与用户问题最相关的文本片段（用于RAG检索）"""
        rows = self.search_dispatch(character_id, query, top_k=top_k)
        return [row["text"] for row in rows]

    def search_with_meta(self, character_id: int, query: str, top_k: int | None = None, mode: str | None = None) -> list[dict[str, object]]:
        """在Milvus中搜索并返回带元数据的知识片段（用于参考文献展示）。
        返回列表中的每个字典包含：source_file, chunk_index, score, text, method。
        """
        return self.search_dispatch(character_id, query, top_k=top_k, mode=mode)

    def has_data(self, character_id: int) -> bool:
        """检查指定角色在Milvus中是否已有向量数据（每个角色独立集合）"""
        from pymilvus import Collection, connections, utility
        try:
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        except Exception:
            return False
        coll_name = self._collection_for(character_id)
        if not utility.has_collection(coll_name):
            return False
        collection = Collection(coll_name)
        collection.load()
        return collection.num_entities > 0

    @staticmethod
    @lru_cache(maxsize=1)
    def _stopwords() -> set[str]:
        words = {
            "的", "了", "和", "是", "在", "也", "就", "都", "而", "及", "与", "着", "或", "一个", "我们", "你们", "他们", "以及",
            "什么", "怎么", "如何", "可以", "是否", "有没有", "请问", "帮我", "告诉我", "对于", "这个", "那个",
        }
        return words

    def _extract_keywords(self, text: str, top_n: int = 8) -> str:
        """为chunk提取关键词，供关键词检索使用。"""
        try:
            import jieba.analyse
            keywords = jieba.analyse.extract_tags(text, topK=top_n)
            if keywords:
                return " ".join(k.strip() for k in keywords if k.strip())
        except Exception:
            pass
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text)
        stopwords = self._stopwords()
        tokens = [t for t in tokens if t not in stopwords and len(t) > 1]
        return " ".join(tokens[:top_n])

    def _tokenize_query(self, query: str) -> list[str]:
        """对查询文本分词，用于关键词检索。"""
        try:
            import jieba
            tokens = [t.strip() for t in jieba.lcut(query) if t.strip()]
        except Exception:
            tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", query)
        stopwords = self._stopwords()
        return [t for t in tokens if t not in stopwords and len(t) > 1]

    @staticmethod
    def _normalize_score(score: float, min_score: float, max_score: float) -> float:
        if max_score <= min_score:
            return 0.0
        return (score - min_score) / (max_score - min_score)

    def _get_bm25_cache(self, character_id: int) -> dict:
        """获取 BM25 文档缓存，每 300 秒刷新一次"""
        import time as _time
        cached = _bm25_cache.get(character_id)
        if cached and (_time.time() - cached.get("ts", 0)) < 300:
            return cached
        from pymilvus import Collection, connections, utility
        try:
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        except Exception:
            return {}
        coll_name = self._collection_for(character_id)
        if not utility.has_collection(coll_name):
            return {}
        collection = Collection(coll_name)
        collection.load()
        existing_fields = {f.name for f in collection.schema.fields}
        kw_output = ["text", "source_file"]
        if "keywords" in existing_fields:
            kw_output.append("keywords")
        if "chunk_index" in existing_fields:
            kw_output.append("chunk_index")
        rows = collection.query(expr="", output_fields=kw_output, limit=2000)
        if not rows:
            return {}
        doc_tokens_list = []
        for row in rows:
            text = str(row.get("text", ""))
            keywords = str(row.get("keywords", ""))
            haystack = f"{keywords} {text}".lower()
            doc_toks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", haystack)
            doc_tokens_list.append(doc_toks)
        avgdl = sum(len(dt) for dt in doc_tokens_list) / max(len(rows), 1)
        cache_entry = {"rows": rows, "doc_tokens": doc_tokens_list, "avgdl": avgdl, "ts": _time.time()}
        _bm25_cache[character_id] = cache_entry
        return cache_entry

    def search_keyword(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """BM25 全文检索：从缓存中获取文档后用 BM25 算法计算相关性。
        BM25 参数: k1=1.2, b=0.75（经典 Okapi BM25 设置）
        使用文档缓存避免重复拉取 Milvus。
        选择 BM25 的原因：
        - 对公司名、人名、年份、金额、股权比例等“精确词面匹配”非常敏感；
        - 相比纯向量检索，BM25 不容易把相似但不包含关键数字的片段排到前面；
        - 作为向量检索的互补通道，能显著提升财报/招股书问答中的数字类问题召回。
        """
        import math
        if top_k is None:
            top_k = settings.retrieval_top_k
        tokens = self._tokenize_query(query)
        if not tokens:
            return []
        cache = self._get_bm25_cache(character_id)
        if not cache:
            return []
        rows = cache["rows"]
        doc_tokens_list = cache["doc_tokens"]
        avgdl = cache["avgdl"]

        # ---- BM25 参数 ----
        k1, b = 1.2, 0.75
        N = len(rows)

        # 统计每个查询词的文档频率 df
        df: dict[str, int] = {}
        for token in tokens:
            tl = token.lower()
            cnt = sum(1 for dt in doc_tokens_list if tl in " ".join(dt))
            df[tl] = cnt

        # 计算每篇文档的 BM25 得分
        scored: list[dict[str, object]] = []
        for idx, row in enumerate(rows):
            doc_toks = doc_tokens_list[idx]
            dl = len(doc_toks)
            doc_text = " ".join(doc_toks)
            score = 0.0
            for token in tokens:
                tl = token.lower()
                tf = doc_text.count(tl)
                if tf == 0:
                    continue
                n_q = df.get(tl, 0)
                idf = math.log((N - n_q + 0.5) / (n_q + 0.5) + 1.0)
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avgdl, 1)))
                score += idf * tf_norm
            if score <= 0:
                continue
            scored.append({
                "text": str(row.get("text", "")),
                "score": score,
                "source_file": row.get("source_file", ""),
                "chunk_index": row.get("chunk_index", 0),
                "keywords": str(row.get("keywords", "")),
                "method": "keyword_bm25",
            })
        scored.sort(key=lambda x: float(x["score"]), reverse=True)
        return scored[:top_k]

    def search_vector(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """向量检索：从Milvus中搜索语义最相关的文本片段（每个角色独立集合）。
        选择向量检索的原因：
        - 可以处理用户问题和原文表述不完全一致的情况，例如“主营业务”与“主要从事”；
        - 相比只用关键词，向量能理解同义表达、上下位概念和自然语言问句；
        - 每个角色独立 collection，避免不同角色知识互相污染，也便于按角色删除/重建知识库。
        这里使用 COSINE，是因为 embedding 语义相似度通常更关注方向而不是向量长度。
        """
        from pymilvus import Collection, connections, utility
        if top_k is None:
            top_k = settings.retrieval_top_k
        try:
            connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        except Exception:
            return []
        coll_name = self._collection_for(character_id)
        if not utility.has_collection(coll_name):
            return []
        collection = Collection(coll_name)
        collection.load()
        query_vector = self._embed(query)
        results = collection.search(
            data=[query_vector],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            output_fields=[f.name for f in collection.schema.fields if f.name in ("text", "source_file", "chunk_index", "keywords")],
        )
        rows: list[dict[str, object]] = []
        for hits in results:
            for rank, hit in enumerate(hits, start=1):
                rows.append({
                    "text": hit.entity.get("text", ""),
                    "score": float(getattr(hit, "distance", 0.0)),
                    "rank": rank,
                    "source_file": hit.entity.get("source_file", ""),
                    "chunk_index": hit.entity.get("chunk_index", 0),
                    "keywords": hit.entity.get("keywords", ""),
                    "method": "vector",
                })
        return rows

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
        """
        # 导入 ThreadPoolExecutor，用于创建线程池，实现并行执行任务（提高检索效率）
        from concurrent.futures import ThreadPoolExecutor
        # 如果没有指定 top_k（返回的结果数量），则使用配置文件中的默认值
        if top_k is None:
            top_k = settings.retrieval_top_k
        # 导入 Neo4jGraphService（图数据库服务类），用于从 Neo4j 中检索关系数据
        from app.services.neo4j_graph_service import Neo4jGraphService
        # 创建一个最大工作线程数为 3 的线程池（同时执行 3 个检索任务）
        with ThreadPoolExecutor(max_workers=3) as pool:
            # 提交关键词检索任务到线程池，返回一个 Future 对象
            # self.search_keyword: BM25 关键词检索方法
            # 参数：character_id 表示角色 ID，query 表示检索问题，max(top_k, 8) 表示至少召回 8 条候选
            kw_future = pool.submit(self.search_keyword, character_id, query, max(top_k, 8))
            # 提交向量检索任务到线程池（语义相似度检索）
            # self.search_vector: 向量检索方法（Embedding + Milvus）
            vec_future = pool.submit(self.search_vector, character_id, query, max(top_k, 8))
            # 提交 Neo4j 图数据库检索任务
            # Neo4jGraphService().search_rows: 从 Neo4j 知识图谱中检索角色相关的实体关系，失败时返回空列表
            # neo4j_top_k: 从配置中读取，控制 Neo4j 最多返回的关系条数
            neo4j_future = pool.submit(Neo4jGraphService().search_rows, character_id, query,
                                       max(top_k, settings.neo4j_top_k))
            # 统一获取三路召回结果；result() 会等待对应任务完成
            # 获取关键字检索的结果（阻塞等待，直到该任务完成）
            kw_rows = kw_future.result()
            # 获取向量检索的结果（阻塞等待）
            vec_rows = vec_future.result()
            # 获取 Neo4j 检索的结果（阻塞等待）
            neo4j_rows = neo4j_future.result()
        # 用 max(top_k, 8) 扩大召回量：多路各取足够候选，为后续融合和 rerank 留出冗余。
        # 并行执行：BM25、向量检索和 Neo4j 图谱检索互不依赖，可降低整体等待时间。

        logger.info("[Hybrid] query=%s, keyword_hits=%d, vector_hits=%d, neo4j_hits=%d", query[:60], len(kw_rows), len(vec_rows), len(neo4j_rows))
        for i, r in enumerate(kw_rows[:5], 1):
            logger.debug("  [BM25  %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])
        for i, r in enumerate(vec_rows[:5], 1):
            logger.debug("  [ANN   %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])
        for i, r in enumerate(neo4j_rows[:5], 1):
            logger.debug("  [Neo4j %d] score=%.4f text=%s...", i, float(r.get("score", 0)), str(r.get("text", ""))[:80])

        merged: dict[str, dict[str, object]] = {}

        for row in kw_rows:
            text = str(row.get("text", ""))
            if not text:
                continue
            merged[text] = {
                **row,
                "vector_score": 0.0,
                "keyword_score": float(row.get("score", 0.0)),
                "hybrid_score": float(row.get("score", 0.0)) * settings.hybrid_keyword_weight,
            }

        vec_scores = [float(r.get("score", 0.0)) for r in vec_rows]
        min_vec = min(vec_scores) if vec_scores else 0.0
        max_vec = max(vec_scores) if vec_scores else 0.0

        for row in vec_rows:
            text = str(row.get("text", ""))
            if not text:
                continue
            normalized_vec = self._normalize_score(float(row.get("score", 0.0)), min_vec, max_vec)
            existing = merged.get(text)
            if existing:
                existing["vector_score"] = normalized_vec
                existing["hybrid_score"] = float(existing.get("hybrid_score", 0.0)) + normalized_vec * settings.hybrid_vector_weight
                existing["method"] = "hybrid"
            else:
                merged[text] = {
                    **row,
                    "vector_score": normalized_vec,
                    "keyword_score": 0.0,
                    "hybrid_score": normalized_vec * settings.hybrid_vector_weight,
                    "method": "vector",
                }

        for row in neo4j_rows:
            text = str(row.get("text", ""))
            if not text:
                continue
            existing = merged.get(text)
            if existing:
                existing["graph_score"] = 1.0
                existing["hybrid_score"] = float(existing.get("hybrid_score", 0.0)) + 1.0
                existing["method"] = "hybrid_graph"
            else:
                merged[text] = {
                    **row,
                    "vector_score": 0.0,
                    "keyword_score": 0.0,
                    "graph_score": 1.0,
                    "hybrid_score": 1.0,
                    "method": "neo4j_graph",
                }

        final_rows = sorted(merged.values(), key=lambda x: float(x.get("hybrid_score", 0.0)), reverse=True)
        candidates = final_rows[:max(top_k, settings.rerank_top_k)]
        logger.info("[Hybrid] merged=%d, candidates=%d (for rerank)", len(final_rows), len(candidates))
        for i, r in enumerate(candidates, 1):
            logger.debug("  [Fused %d] hybrid=%.4f method=%-8s text=%s...", i, float(r.get("hybrid_score", 0)), r.get("method", ""), str(r.get("text", ""))[:80])
        return self._rerank(query, candidates, top_n=top_k)

    def _rerank(self, query: str, rows: list[dict[str, object]], top_n: int | None = None) -> list[dict[str, object]]:
        """调用 SiliconFlow rerank API 对候选文档重新精排。
        失败时静默回退到混合检索分数排序，不阻塞主流程。
        为什么在混合检索后再做 Rerank：
        - 第一阶段检索强调“召回”，宁可多取一些候选，避免漏掉答案；
        - Rerank 模型逐对判断“问题-片段”的相关性，比简单分数融合更接近真实问答需求；
        - 相比直接让 LLM 在大量片段里找答案，Rerank 成本更低，也能减少上下文长度和幻觉风险。
        这里保留 fallback，是因为 RAG 系统不能强依赖外部重排序 API；即使 Rerank 超时或失败，
        仍可用 BM25+向量融合结果继续回答，保证可用性优先。
        """
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
                    row["method"] = "rerank"
                    reranked.append(row)
            logger.info("[Rerank] done: input=%d, output=%d, model=%s", len(documents), len(reranked), settings.rerank_model)
            for i, r in enumerate(reranked, 1):
                logger.info("  [Rerank %d] score=%.4f text=%s...", i, float(r.get("rerank_score", 0)), str(r.get("text", ""))[:80])
            return reranked
        except Exception as e:
            logger.warning("Rerank API failed, fallback to hybrid score: %s", e)
            return rows[:top_n]

    @staticmethod
    def _prepare_embedding_input(text: str, max_chars: int) -> str:
        prepared = re.sub(r"\s+", " ", text).strip()
        prepared = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", prepared)
        return prepared[:max_chars]

    def _embed(self, text: str, *, use_cache: bool = True) -> list[float]:
        """将文本转换为向量：优先查缓存 → 调用Embedding API → 退化为SHA256伪向量"""
        embed_text = self._prepare_embedding_input(text, 1000)
        cache_key = hashlib.md5(embed_text.encode("utf-8")).hexdigest()
        if use_cache and cache_key in _embed_cache:
            _embed_cache.move_to_end(cache_key)  # 刷新 LRU 顺序
            return _embed_cache[cache_key]
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if base_url and api_key:
            import httpx
            url = f"{base_url}/embeddings"
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                with httpx.Client(timeout=15.0, trust_env=False) as client:
                    last_error: Exception | None = None
                    for max_chars in (1000, 600, 300):
                        candidate = self._prepare_embedding_input(text, max_chars)
                        if not candidate:
                            continue
                        payload = {"model": settings.embedding_model_name, "input": candidate}
                        try:
                            resp = client.post(url, headers=headers, json=payload)
                            resp.raise_for_status()
                            data = resp.json()
                            vec = data["data"][0]["embedding"][:settings.milvus_dim]
                            self._cache_embed_result(cache_key, vec)
                            return vec
                        except httpx.HTTPStatusError as exc:
                            last_error = exc
                            if exc.response.status_code != 413:
                                raise
                    if last_error:
                        raise last_error
            except Exception as e:
                logger.warning("Embedding API failed, falling back to SHA256: %s", e)
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector: list[float] = []
        for i in range(settings.milvus_dim):
            byte = digest[i % len(digest)]
            vector.append((byte / 255.0) * 2 - 1)
        return vector

    def _cache_embed_result(self, cache_key: str, vec: list[float]) -> None:
        """将 embedding 结果存入 LRU 缓存"""
        _embed_cache[cache_key] = vec
        if len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)

    def _insert_into_milvus(self, rows: list[dict[str, object]], character_id: int) -> None:
        """将向量数据批量写入Milvus（每个角色独立集合，如果集合不存在或维度不匹配会自动创建/重建）"""
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

        connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        coll_name = self._collection_for(character_id)
        need_create = False
        if utility.has_collection(coll_name):
            existing = Collection(coll_name)
            for f in existing.schema.fields:
                if f.name == "vector" and f.params.get("dim") != settings.milvus_dim:
                    utility.drop_collection(coll_name)
                    need_create = True
                    break
        else:
            need_create = True
        if need_create:
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),  # Milvus 自动生成的主键ID
                FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=255),  # 来源文件名，用于展示引用出处
                FieldSchema(name="chunk_index", dtype=DataType.INT64),  # 文本块序号，用于定位原文位置
                FieldSchema(name="chunk_hash", dtype=DataType.VARCHAR, max_length=64),  # 文本 SHA256 指纹，用于去重
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),  # 原始文本块，作为 RAG 上下文返回
                FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=4096),  # 关键词串，用于 BM25 关键词检索
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=settings.milvus_dim),  # 语义向量字段，用于 ANN 相似度检索
            ]
            schema = CollectionSchema(fields, description=f"Knowledge base for character {character_id}")
            collection = Collection(name=coll_name, schema=schema)
        else:
            collection = Collection(coll_name)
        if need_create:
            index_params = {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
            collection.create_index(field_name="vector", index_params=index_params)
        collection.load()
        columns = [
            [row["source_file"] for row in rows],  # source_file 列：每个 chunk 的来源 PDF
            [row.get("chunk_index", 0) for row in rows],  # chunk_index 列：每个 chunk 的顺序编号
            [row["chunk_hash"] for row in rows],  # chunk_hash 列：每个 chunk 的去重指纹
            [row["text"] for row in rows],  # text 列：实际可被检索和送入 prompt 的文本
            [row.get("keywords", "") for row in rows],  # keywords 列：BM25 使用的关键词补充
            [row["vector"] for row in rows],  # vector 列：Milvus 建索引和向量搜索使用
        ]
        collection.insert(columns)
        collection.flush()
