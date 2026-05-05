"""
Embedding 微调训练集生成脚本

从 Milvus 中的知识库片段 + 评估问答对自动构造：
1. 正例对 (query, positive_passage) — 问题与正确答案片段
2. 难负例 (query, negative_passage) — 问题与无关但相似的片段
3. 三元组 (anchor, positive, negative) — 用于对比学习

输出格式：JSONL，兼容 sentence-transformers 训练

用法：python scripts/build_finetune_dataset.py
"""

import sys, os, json, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService

CHARACTER_ID = 6
pdf_svc = PDFIngestService()

# ========== 预定义问答对（正例来源）==========
QA_PAIRS = [
    {"query": "公司的全称是什么？注册地在哪里？", "keywords": ["武汉兴图新科电子股份有限公司", "东湖新技术开发区"]},
    {"query": "本次发行的股票数量是多少？", "keywords": ["1,840"]},
    {"query": "本次发行的保荐机构是哪家？", "keywords": ["中泰证券"]},
    {"query": "募集资金主要用于哪些项目？", "keywords": ["云联邦", "军用视频指挥", "研发中心"]},
    {"query": "公司的主营业务是什么？", "keywords": ["视频指挥控制系统"]},
    {"query": "公司的控股股东和实际控制人是谁？", "keywords": ["程家明"]},
    {"query": "报告期内公司的营业收入是多少？", "keywords": ["19,813"]},
    {"query": "公司有哪些主要的风险因素？", "keywords": ["客户集中", "应收账款"]},
    {"query": "公司的核心竞争力有哪些？", "keywords": ["核心技术", "中间件"]},
    {"query": "公司应收账款规模较大的原因是什么？", "keywords": ["军方", "结算周期"]},
    {"query": "2018年末公司的应收账款余额是多少？", "keywords": ["18,597"]},
    {"query": "公司前五大客户的收入占比是多少？", "keywords": ["前五"]},
    {"query": "公司的员工总人数和研发人员占比是多少？", "keywords": ["研发", "占比"]},
    {"query": "公司的注册资本是多少？", "keywords": ["5,520"]},
    {"query": "公司成立时间是什么时候？", "keywords": ["2003"]},
    {"query": "公司的实际控制人持股比例是多少？", "keywords": ["程家明", "持股"]},
    {"query": "公司的主要竞争对手有哪些？", "keywords": ["竞争"]},
    {"query": "公司的毛利率是多少？", "keywords": ["毛利"]},
    {"query": "公司的研发费用占比是多少？", "keywords": ["研发", "费用"]},
    {"query": "募集资金总额是多少？", "keywords": ["募集", "资金"]},
]


def get_all_chunks():
    """从 Milvus 获取所有知识片段"""
    from pymilvus import Collection, connections, utility
    connections.connect(alias="default", uri=settings.milvus_url, db_name=settings.milvus_db)
    coll_name = f"{settings.milvus_collection}_{CHARACTER_ID}"
    if not utility.has_collection(coll_name):
        return []
    collection = Collection(coll_name)
    collection.load()
    rows = collection.query(
        expr="",
        output_fields=["text"],
        limit=2000,
    )
    return [str(r.get("text", "")) for r in rows if r.get("text")]


def find_positive(chunks, keywords):
    """找到包含关键词最多的片段作为正例"""
    best_chunk = ""
    best_score = 0
    for chunk in chunks:
        score = sum(1 for kw in keywords if kw in chunk)
        if score > best_score:
            best_score = score
            best_chunk = chunk
    return best_chunk


def find_hard_negatives(chunks, keywords, positive, n=3):
    """找到不含关键词但与正例长度相近的片段作为难负例"""
    negatives = []
    pos_len = len(positive)
    candidates = [c for c in chunks if c != positive and not any(kw in c for kw in keywords)]
    # 按长度相近排序
    candidates.sort(key=lambda c: abs(len(c) - pos_len))
    return candidates[:n]


def main():
    print("[INFO] 从 Milvus 获取知识片段...")
    chunks = get_all_chunks()
    if not chunks:
        print("[ERROR] 没有找到知识片段")
        sys.exit(1)
    print(f"[INFO] 获取到 {len(chunks)} 个片段")

    pairs_data = []      # {query, positive}
    triplets_data = []   # {anchor, positive, negative}

    for qa in QA_PAIRS:
        query = qa["query"]
        keywords = qa["keywords"]

        positive = find_positive(chunks, keywords)
        if not positive:
            continue

        # 正例对
        pairs_data.append({
            "query": query,
            "positive": positive[:512],
            "label": 1.0,
        })

        # 难负例三元组
        negatives = find_hard_negatives(chunks, keywords, positive)
        for neg in negatives:
            triplets_data.append({
                "anchor": query,
                "positive": positive[:512],
                "negative": neg[:512],
            })

        # 额外：用正例片段中的句子作为 query 变体（数据增强）
        sentences = [s.strip() for s in positive.split("。") if len(s.strip()) > 10]
        for sent in sentences[:2]:
            pairs_data.append({
                "query": sent,
                "positive": positive[:512],
                "label": 1.0,
            })

    # 添加随机负例对
    for qa in QA_PAIRS:
        query = qa["query"]
        neg_chunk = random.choice([c for c in chunks if not any(kw in c for kw in qa["keywords"])])
        pairs_data.append({
            "query": query,
            "positive": neg_chunk[:512],
            "label": 0.0,
        })

    # 保存
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "finetune")
    os.makedirs(output_dir, exist_ok=True)

    pairs_path = os.path.join(output_dir, "pairs.jsonl")
    with open(pairs_path, "w", encoding="utf-8") as f:
        for item in pairs_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    triplets_path = os.path.join(output_dir, "triplets.jsonl")
    with open(triplets_path, "w", encoding="utf-8") as f:
        for item in triplets_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n[DONE] 训练集生成完成：")
    print(f"  - 正例/负例对: {len(pairs_data)} 条 → {pairs_path}")
    print(f"  - 三元组: {len(triplets_data)} 条 → {triplets_path}")


if __name__ == "__main__":
    main()
