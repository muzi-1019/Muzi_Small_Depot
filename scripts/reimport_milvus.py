"""
重新导入 PDF 数据到 Milvus 的一次性工具脚本。

用途：当 Milvus 集合结构变更（如新增 chunk_index、keywords 字段）后，
需要重新解析 PDF 并写入新结构的集合。本脚本会：
1. 读取指定的招股说明书 PDF 文件
2. 调用 PDFIngestService 重新解析、切分、向量化并写入 Milvus
3. 验证导入结果（打印 schema 字段和样本数据）

用法：python scripts/reimport_milvus.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("RAG_MILVUS_URL", "http://localhost:19530")  # 默认使用本地 Milvus

from pathlib import Path
from app.services.pdf_ingest_service import PDFIngestService

svc = PDFIngestService()

# 使用与测试集匹配的兴图新科招股说明书
pdf_path = Path("data/招股说明书附件/招股说明书1-无水印.pdf")
print(f"Using PDF: {pdf_path}")

# 执行导入：解析 PDF → 切分文本 → 向量化 → 写入 Milvus
count = svc.ingest_file(character_id=6, pdf_path=pdf_path)
print(f"Imported {count} chunks")

# 验证导入结果：连接 Milvus 并查询样本数据，确认新字段已正确写入
from pymilvus import connections, Collection
connections.connect(uri="http://localhost:19530")
c = Collection("character_knowledge_6")
print("Schema fields:", [f.name for f in c.schema.fields])
c.load()
sample = c.query(expr="", output_fields=["chunk_index", "keywords", "source_file"], limit=5)
for r in sample:
    ci = r.get("chunk_index", "?")
    kw = r.get("keywords", "")[:80]
    print(f"  chunk_index={ci}  keywords={kw}")
# from pymilvus import connections, utility
#
# connections.connect(uri="http://192.168.35.187:19530")
# if utility.has_collection("character_knowledge"):
#     utility.drop_collection("character_knowledge")
#     print("旧集合已删除")
