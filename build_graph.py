"""
知识图谱构建脚本 — 从 Milvus 中已有的 PDF 知识片段抽取实体关系并构建图谱。
构建完成后保存为 data/graphs/graph_{character_id}.json。

用法：python build_graph.py [character_id]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings
from app.services.graph_service import KnowledgeGraphService

CHARACTER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def main():
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
    print(f"[INFO] 获取到 {len(rows)} 个知识片段")

    if not rows:
        print("[ERROR] 该角色没有知识库数据")
        sys.exit(1)

    chunks = [str(r.get("text", "")) for r in rows if r.get("text")]
    print(f"[INFO] 有效片段: {len(chunks)}")
    print(f"[INFO] 开始抽取实体关系（每5个片段一批）...")

    svc = KnowledgeGraphService()
    graph = svc.build_graph(CHARACTER_ID, chunks, batch_size=5)

    print(f"\n[DONE] 知识图谱构建完成:")
    print(f"  - 实体数: {graph['entity_count']}")
    print(f"  - 关系数: {graph['triple_count']}")
    print(f"  - 保存路径: data/graphs/graph_{CHARACTER_ID}.json")


if __name__ == "__main__":
    main()
