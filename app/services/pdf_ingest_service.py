"""
本文件的作用：PDF 解析与向量入库服务（RAG 知识管道的核心）。
完整处理流程：
1. 读取 PDF 文件，提取文字内容（支持 PyMuPDF 和 pypdf 两种库，还支持 OCR 扫描件识别）
2. 将长文本切分成固定大小的片段（chunks），带有重叠区域避免信息断裂
3. 调用 Embedding API 将每个文本片段转换为向量（数字表示的含义）
4. 将向量和文本写入 Milvus 向量数据库，供后续 RAG 检索使用

同时提供搜索和查询功能：
- search：根据问题搜索最相关的文本片段
- has_data：检查某个角色是否已有知识库数据
"""

from __future__ import annotations  # 允许在类型注解中使用字符串形式的类型

import hashlib                    # 哈希算法库，用于生成文本指纹和备用向量
import re                         # 正则表达式，用于文本清洗
from dataclasses import dataclass  # 数据类装饰器
from pathlib import Path           # 文件路径处理
from functools import lru_cache     # 缓存工具

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
        self.collection_name = settings.milvus_collection  # Milvus 中的集合名称

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
        text = self._extract_text(pdf_path)
        chunks = self._chunk_text(text)
        if not chunks:
            return 0
        rows = [self._build_row(character_id, pdf_path, chunk, chunk_index) for chunk_index, chunk in enumerate(chunks)]
        self._insert_into_milvus(rows)
        return len(rows)

    def _role_pdf_mapping(self) -> dict[int, Path]:
        """预定义的角色ID与PDF文件的映射关系（硬编码的初始知识库配置）"""
        data_dir = Path(settings.data_dir)
        return {
            2: data_dir / "国家基层高血压防治管理手册2025版.pdf",
            3: data_dir / "中华人民共和国宪法.pdf",
        }

    def _extract_text(self, pdf_path: Path) -> str:
        """从PDF文件中提取全部文字内容（优先用PyMuPDF，不可用时用pypdf，扫描件用OCR识别）"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        doc = fitz.open(str(pdf_path))
        ocr_engine = None
        pages: list[str] = []
        for page in doc:
            text = page.get_text("text") or ""
            if len(text.strip()) < 30:
                ocr_text = self._ocr_page(page, ocr_engine)
                if ocr_text is not None:
                    if ocr_engine is None:
                        ocr_engine = self._get_ocr_engine()
                    ocr_text = self._ocr_page(page, ocr_engine)
                text = ocr_text if ocr_text else text
            pages.append(text)
        doc.close()
        return "\n".join(pages)

    @staticmethod
    def _get_ocr_engine():
        """获取OCR引擎实例（用于识别扫描版PDF中的图片文字）"""
        try:
            from rapidocr_onnxruntime import RapidOCR
            return RapidOCR()
        except ImportError:
            return None

    @staticmethod
    def _ocr_page(page, ocr_engine) -> str | None:
        """对单个PDF页面进行OCR文字识别（当普通文本提取结果太少时使用）"""
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
        """将长文本切分成固定大小的片段，相邻片段之间有重叠区域（避免关键信息被切断）"""
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
        """为单个文本片段构建完整的数据行（包含角色ID、来源文件、文本、向量、哈希指纹）"""
        keywords = self._extract_keywords(chunk_text)
        return {
            "character_id": character_id,
            "source_file": pdf_path.name,
            "chunk_index": chunk_index,
            "text": chunk_text,
            "keywords": keywords,
            "vector": self._embed(chunk_text),
            "chunk_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
        }

    def search(self, character_id: int, query: str, top_k: int | None = None) -> list[str]:
        """在Milvus中搜索与用户问题最相关的文本片段（用于RAG检索）"""
        rows = self.search_hybrid(character_id, query, top_k=top_k)
        return [row["text"] for row in rows]

    def has_data(self, character_id: int) -> bool:
        """检查指定角色在Milvus中是否已有向量数据（用于判断是否需要执行RAG检索）"""
        from pymilvus import Collection, connections, utility
        try:
            connections.connect(alias="default", uri=settings.milvus_uri, db_name=settings.milvus_db)
        except Exception:
            return False
        if not utility.has_collection(self.collection_name):
            return False
        collection = Collection(self.collection_name)
        collection.load()
        res = collection.query(expr=f"character_id == {character_id}", output_fields=["character_id"], limit=1)
        return len(res) > 0

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

    def search_keyword(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """轻量关键词检索：在chunk关键词和正文中做词项匹配。"""
        from pymilvus import Collection, connections, utility
        if top_k is None:
            top_k = settings.retrieval_top_k
        try:
            connections.connect(alias="default", uri=settings.milvus_uri, db_name=settings.milvus_db)
        except Exception:
            return []
        if not utility.has_collection(self.collection_name):
            return []
        collection = Collection(self.collection_name)
        collection.load()
        tokens = self._tokenize_query(query)
        if not tokens:
            return []
        expr = f"character_id == {character_id}"
        rows = collection.query(expr=expr, output_fields=["text", "keywords", "source_file", "chunk_index"], limit=2000)
        scored: list[dict[str, object]] = []
        for row in rows:
            text = str(row.get("text", ""))
            keywords = str(row.get("keywords", ""))
            haystack = f"{keywords} {text}".lower()
            hits = sum(1 for token in tokens if token.lower() in haystack)
            if hits == 0:
                continue
            score = hits / max(len(tokens), 1)
            scored.append({
                "text": text,
                "score": score,
                "source_file": row.get("source_file", ""),
                "chunk_index": row.get("chunk_index", 0),
                "keywords": keywords,
                "method": "keyword",
            })
        scored.sort(key=lambda x: float(x["score"]), reverse=True)
        return scored[:top_k]

    def search_vector(self, character_id: int, query: str, top_k: int | None = None) -> list[dict[str, object]]:
        """向量检索：从Milvus中搜索语义最相关的文本片段。"""
        from pymilvus import Collection, connections, utility
        if top_k is None:
            top_k = settings.retrieval_top_k
        try:
            connections.connect(alias="default", uri=settings.milvus_uri, db_name=settings.milvus_db)
        except Exception:
            return []
        if not utility.has_collection(self.collection_name):
            return []
        collection = Collection(self.collection_name)
        collection.load()
        query_vector = self._embed(query)
        results = collection.search(
            data=[query_vector],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            expr=f"character_id == {character_id}",
            output_fields=["text", "source_file", "chunk_index", "keywords"],
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
        """混合检索：合并关键词和向量结果后按融合分数排序。"""
        if top_k is None:
            top_k = settings.retrieval_top_k
        kw_rows = self.search_keyword(character_id, query, top_k=max(top_k, 8))
        vec_rows = self.search_vector(character_id, query, top_k=max(top_k, 8))

        merged: dict[str, dict[str, object]] = {}

        for row in kw_rows:
            text = str(row.get("text", ""))
            if not text:
                continue
            merged[text] = {
                **row,
                "vector_score": 0.0,
                "keyword_score": float(row.get("score", 0.0)),
                "hybrid_score": float(row.get("score", 0.0)) * 0.4,
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
                existing["hybrid_score"] = float(existing.get("hybrid_score", 0.0)) + normalized_vec * 0.6
                existing["method"] = "hybrid"
            else:
                merged[text] = {
                    **row,
                    "vector_score": normalized_vec,
                    "keyword_score": 0.0,
                    "hybrid_score": normalized_vec * 0.6,
                    "method": "vector",
                }

        final_rows = sorted(merged.values(), key=lambda x: float(x.get("hybrid_score", 0.0)), reverse=True)
        return final_rows[:top_k]

    def _embed(self, text: str) -> list[float]:
        """将文本转换为向量：优先调用Embedding API，API不可用时退化为SHA256哈希生成的伪向量"""
        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if base_url and api_key:
            import httpx
            url = f"{base_url}/embeddings"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"model": settings.embedding_model_name, "input": text[:2000]}
            try:
                with httpx.Client(timeout=15.0, trust_env=False) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    vec = data["data"][0]["embedding"]
                    return vec[:settings.milvus_dim]
            except Exception:
                pass
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector: list[float] = []
        for i in range(settings.milvus_dim):
            byte = digest[i % len(digest)]
            vector.append((byte / 255.0) * 2 - 1)
        return vector

    def _insert_into_milvus(self, rows: list[dict[str, object]]) -> None:
        """将向量数据批量写入Milvus（如果集合不存在或维度不匹配会自动创建/重建）"""
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

        connections.connect(alias="default", uri=settings.milvus_uri, db_name=settings.milvus_db)
        need_create = False
        if utility.has_collection(self.collection_name):
            existing = Collection(self.collection_name)
            for f in existing.schema.fields:
                if f.name == "vector" and f.params.get("dim") != settings.milvus_dim:
                    utility.drop_collection(self.collection_name)
                    need_create = True
                    break
        else:
            need_create = True
        if need_create:
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="character_id", dtype=DataType.INT64),
                FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=255),
                FieldSchema(name="chunk_hash", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=settings.milvus_dim),
            ]
            schema = CollectionSchema(fields, description="Role PDF knowledge base")
            collection = Collection(name=self.collection_name, schema=schema)
        else:
            collection = Collection(self.collection_name)
        collection.create_index(field_name="vector")  # 关键修复
        collection.load()
        columns = [
            [row["character_id"] for row in rows],
            [row["source_file"] for row in rows],
            [row["chunk_hash"] for row in rows],
            [row["text"] for row in rows],
            [row["vector"] for row in rows],
        ]
        collection.insert(columns)
        collection.flush()
