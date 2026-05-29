"""切换 bge-m3 本地模型后的完整验证脚本"""
import sys, logging, time
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService

svc = PDFIngestService()

# ─── 1. 本地 ONNX Embedding ───────────────────────────────────────
print("=== [1/4] 本地 ONNX Embedding ===")
t0 = time.time()
vec = svc._embed("武汉兴图新科的主营业务是什么？", use_cache=False)
elapsed = time.time() - t0
ok1 = len(vec) == 1024
print(f"  维度: {len(vec)}  耗时: {elapsed:.2f}s  {'OK' if ok1 else 'FAIL'}")

t0 = time.time()
svc._embed("武汉兴图新科的主营业务是什么？")   # 命中缓存
cache_t = time.time() - t0
print(f"  缓存命中耗时: {cache_t:.4f}s  {'OK' if cache_t < 0.01 else 'slow'}")

# ─── 2. Milvus 连通性 ─────────────────────────────────────────────
print()
print("=== [2/4] Milvus 连通性 ===")
ok2 = False
cols = []
try:
    from pymilvus import connections, utility
    connections.connect("default", uri=settings.milvus_url)
    cols = utility.list_collections()
    print(f"  URL: {settings.milvus_url}")
    print(f"  已有集合: {cols}  OK")
    ok2 = True
except Exception as e:
    print(f"  FAIL: {e}")

# ─── 3. 向量检索验证 ──────────────────────────────────────────────
print()
print("=== [3/4] 向量检索（用新 bge-m3 向量查旧数据）===")
ok3 = False
if ok2 and cols:
    try:
        from pymilvus import Collection
        col_name = cols[0]
        col = Collection(col_name)
        col.load()
        res = col.search(
            data=[vec], anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 8}},
            limit=3, output_fields=["text"]
        )
        hits = res[0]
        print(f"  集合: {col_name}  命中: {len(hits)} 条")
        for i, h in enumerate(hits):
            score = round(float(h.score), 4)
            snippet = str(h.entity.get("text", ""))[:70]
            print(f"  [{i+1}] score={score}  text={snippet}...")
        # score < 0.5 说明向量空间不一致，需要重新入库
        max_score = max(float(h.score) for h in hits) if hits else 0
        if max_score < 0.5:
            print()
            print("  *** 警告：最高相似度 < 0.5，旧向量与新模型不兼容 ***")
            print("  *** 需要重新入库 PDF（见下方建议） ***")
        else:
            ok3 = True
            print("  OK")
    except Exception as e:
        print(f"  FAIL: {e}")
else:
    print("  跳过（Milvus 不可用或无集合）")

# ─── 4. 配置一致性检查 ────────────────────────────────────────────
print()
print("=== [4/4] 配置一致性 ===")
dim_ok = settings.milvus_dim == len(vec)
print(f"  embedding_local_model_path : {settings.embedding_local_model_path}")
print(f"  embedding_model_name       : {settings.embedding_model_name}  (API 回退用)")
print(f"  milvus_dim                 : {settings.milvus_dim}  向量实际维度: {len(vec)}  {'OK' if dim_ok else 'MISMATCH'}")

# ─── 总结 ─────────────────────────────────────────────────────────
print()
print("=" * 50)
print("结论：")
print(f"  [1] 本地 ONNX Embedding : {'OK' if ok1 else 'FAIL'}")
print(f"  [2] Milvus 连通性       : {'OK' if ok2 else 'FAIL'}")
print(f"  [3] 向量检索            : {'OK' if ok3 else '需重新入库'}")
print(f"  [4] 维度一致性          : {'OK' if dim_ok else 'FAIL'}")
if not ok3 and ok2 and cols:
    print()
    print(">>> 建议：旧向量用的是不同模型，需重新入库。")
    print(">>> 执行：python scripts/init_pdf_knowledge.py")
    print(">>>       或在前端【知识库管理】页面重新上传 PDF。")
