"""
LightRAG 知识图谱构建脚本 — 增量更新模式

与 build_graph.py 的区别：
1. 支持增量更新（已处理的片段不会重复处理）
2. 自动构建社区（连通分量聚类）
3. 支持双层检索（Local + Global）

用法：python scripts/build_lightrag.py [character_id]
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.config import settings
from app.services.lightrag_service import LightRAGService

CHARACTER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def main():
    """主函数：从 Milvus 获取已有知识片段，调用 LightRAGService 增量构建图谱"""
    from pymilvus import Collection, connections, utility

    print(f"[INFO] 连接 Milvus: {settings.milvus_url}")
    connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)

    collection_name = f"{settings.milvus_collection}_{CHARACTER_ID}"
    if not utility.has_collection(collection_name):
        print(f"[ERROR] 集合 {collection_name} 不存在")
        sys.exit(1)

    collection = Collection(collection_name)
    collection.load()
    rows = collection.query(
        expr="",
        output_fields=["text"],
        limit=2000,
    )
    chunks = [str(r.get("text", "")) for r in rows if r.get("text")]
    print(f"[INFO] 获取到 {len(chunks)} 个知识片段")

    svc = LightRAGService()
    data = svc.ingest_chunks(CHARACTER_ID, chunks)

    print(f"\n[DONE] LightRAG 图谱构建完成:")
    print(f"  - 实体数: {data.get('entity_count', 0)}")
    print(f"  - 关系数: {data.get('relation_count', 0)}")
    print(f"  - 社区数: {data.get('community_count', 0)}")
    print(f"  - 保存路径: data/lightrag/lightrag_{CHARACTER_ID}.json")


if __name__ == "__main__":
    main()
