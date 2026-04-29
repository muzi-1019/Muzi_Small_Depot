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

from dataclasses import dataclass   # 数据类装饰器
from typing import Iterable         # 可迭代类型注解

from app.core.config import settings  # 全局配置


@dataclass
class RetrievedChunk:
    """检索到的知识片段数据结构"""
    text: str            # 片段文本内容
    score: float = 0.0   # 相似度得分（越高越相关）


class RAGService:
    """
    RAG 检索服务：从 Milvus 检索与 PDF 切分后的文本块，并返回适合 LLM 直接使用的上下文。
    约定：Milvus collection 中至少包含字段：character_id, text, vector。
    """

    def retrieve(self, character_id: int, question: str, top_k: int = 5) -> list[str]:
        """
        检索入口：根据角色ID和用户问题，从向量库中检索最相关的知识片段。
        返回格式化后的文本列表，可直接拼接作为大模型的上下文。
        """
        try:
            chunks = self._milvus_search(character_id, question, top_k)  # 执行向量搜索
            if chunks:
                return [self._format_chunk(i + 1, c) for i, c in enumerate(chunks)]  # 格式化结果
        except Exception:
            pass
        return []  # 检索失败返回空列表

    def _milvus_search(self, character_id: int, question: str, top_k: int) -> list[RetrievedChunk]:
        """在 Milvus 中执行向量相似度搜索"""
        from pymilvus import Collection, connections, utility

        connections.connect(alias="default", uri=settings.milvus_uri, db_name=settings.milvus_db)  # 连接 Milvus
        name = settings.milvus_collection
        if not utility.has_collection(name):  # 集合不存在则返回空
            return []

        col = Collection(name)
        col.load()  # 加载集合到内存
        query_vec = self._encode_question(question)  # 将问题转为向量
        search_params = {"metric_type": "IP", "params": {"nprobe": 16}}  # 搜索参数：内积相似度
        results = col.search(
            data=[query_vec],                                          # 查询向量
            anns_field="vector",                                       # 搜索的向量字段名
            param=search_params,                                       # 搜索参数
            limit=max(top_k, settings.rerank_top_k),                   # 返回条数
            expr=f"character_id == {character_id}",                    # 过滤条件：只搜索该角色的知识
            output_fields=["text"],                                    # 同时返回文本字段
        )
        chunks: list[RetrievedChunk] = []
        for hit in results[0] if results else []:  # 遍历搜索结果
            entity = hit.entity
            text = entity.get("text") if entity is not None else ""
            if text:
                chunks.append(RetrievedChunk(text=text, score=float(hit.score)))
        return self._deduplicate(chunks)[:top_k]  # 去重后取前 top_k 条

    def _encode_question(self, question: str) -> list[float]:
        """
        将用户问题编码为向量（占位实现，使用 SHA256 哈希生成伪向量）。
        生产环境建议替换为与入库一致的 Embedding 服务。
        """
        import hashlib

        dim = settings.milvus_dim
        digest = hashlib.sha256(question.encode("utf-8")).digest()  # 计算问题的 SHA256 哈希
        vec = []
        for i in range(dim):
            byte = digest[i % len(digest)]
            vec.append((byte / 255.0) * 2 - 1)  # 将字节值映射到 [-1, 1] 范围
        return vec

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
