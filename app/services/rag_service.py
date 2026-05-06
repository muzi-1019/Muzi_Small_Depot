"""
本文件的作用：RAG 检索服务（Retrieval-Augmented Generation 的检索部分）。
从 Milvus 向量数据库中检索与用户问题最相关的知识片段，
然后格式化为适合大模型直接使用的上下文文本。

工作流程：
1. 将用户问题编码为向量
2. 在 Milvus 中按角色ID过滤，搜索最相似的向量
3. 获取对应的文本片段
4. 去重后格式化返回

注意：本文件中的 _encode_question 使用 SHA256 占位向量，
实际项目中 chat_service.py 调用的是 pdf_ingest_service.py 中使用真实 Embedding API 的 search 方法。
"""

from __future__ import annotations  # 允许字符串形式的类型注解

import logging                       # 日志
from dataclasses import dataclass   # 数据类装饰器
from typing import Iterable         # 可迭代类型注解

from app.core.config import settings  # 全局配置

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """检索到的知识片段数据结构"""
    text: str            # 片段文本内容
    score: float = 0.0   # 相似度得分（越高越相关）


class RAGService:
    """
    RAG 检索服务：从 Milvus 检索与 PDF 切分后的文本块，并返回适合 LLM 直接使用的上下文。
    约定：每个角色独立 Milvus collection（character_knowledge_{id}），包含字段：text, vector。
    """

    def retrieve(self, character_id: int, question: str, top_k: int = 5) -> list[str]:
        """
        检索入口：根据角色ID和用户问题，从向量库中检索最相关的知识片段。
        返回格式化后的文本列表，可直接拼接作为大模型的上下文。
        """
        logger.info("RAG retrieve start: character_id=%d, question=%s, top_k=%d", character_id, question[:60], top_k)
        try:
            chunks = self._milvus_search(character_id, question, top_k)
            if chunks:
                logger.info("RAG retrieve done: found=%d chunks", len(chunks))
                return [self._format_chunk(i + 1, c) for i, c in enumerate(chunks)]
            logger.warning("RAG retrieve empty: no chunks found")
        except Exception as e:
            logger.error("RAG retrieve failed: %s", e, exc_info=True)
        return []

    def _milvus_search(self, character_id: int, question: str, top_k: int) -> list[RetrievedChunk]:
        """在 Milvus 中执行向量相似度搜索"""
        from pymilvus import Collection, connections, utility

        logger.debug("Milvus connect: %s", settings.milvus_url)
        connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
        name = f"{settings.milvus_collection}_{character_id}"
        if not utility.has_collection(name):
            logger.warning("Milvus collection not found: %s", name)
            return []

        col = Collection(name)
        col.load()
        logger.debug("Milvus collection loaded: %s", name)
        query_vec = self._encode_question(question)
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
        results = col.search(
            data=[query_vec],
            anns_field="vector",
            param=search_params,
            limit=max(top_k, settings.rerank_top_k),
            output_fields=["text"],
        )
        chunks: list[RetrievedChunk] = []
        for hit in results[0] if results else []:
            entity = hit.entity
            text = entity.get("text") if entity is not None else ""
            if text:
                chunks.append(RetrievedChunk(text=text, score=float(hit.score)))
        deduped = self._deduplicate(chunks)[:top_k]
        logger.info("Milvus search done: raw=%d, deduped=%d", len(chunks), len(deduped))
        return deduped

    def _encode_question(self, question: str) -> list[float]:
        """
        将用户问题编码为向量：优先调用 Embedding API，失败时退化为 SHA256 伪向量。
        """
        import hashlib
        import httpx

        base_url = (settings.openai_api_base or "").rstrip("/")
        api_key = settings.openai_api_key or ""
        if base_url and api_key:
            try:
                url = f"{base_url}/embeddings"
                headers = {"Authorization": f"Bearer {api_key}"}
                payload = {"model": settings.embedding_model_name, "input": question[:2000]}
                with httpx.Client(timeout=15.0, trust_env=False) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                return data["data"][0]["embedding"][:settings.milvus_dim]
            except Exception as e:
                logger.warning("Embedding API failed, fallback to SHA256: %s", e)
        dim = settings.milvus_dim
        digest = hashlib.sha256(question.encode("utf-8")).digest()
        return [(digest[i % len(digest)] / 255.0) * 2 - 1 for i in range(dim)]

    @staticmethod
    def _deduplicate(chunks: Iterable[RetrievedChunk]) -> list[RetrievedChunk]:
        """对检索结果去重（内容完全相同的片段只保留第一个）"""
        seen: set[str] = set()
        out: list[RetrievedChunk] = []
        for chunk in chunks:
            text = chunk.text.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(chunk)
        return out

    @staticmethod
    def _format_chunk(index: int, chunk: RetrievedChunk) -> str:
        """将单个检索结果格式化为带序号和得分的文本（方便大模型理解）"""
        score = f"{chunk.score:.4f}" if chunk.score else "n/a"
        return f"[{index} | score={score}] {chunk.text.strip()}"
