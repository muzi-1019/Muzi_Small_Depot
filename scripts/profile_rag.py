"""
RAG 性能瓶颈分析脚本（Task 13）

对 RAG 问答的每个阶段进行精确计时：
1. Query Rewriting（指代消解）
2. Embedding（向量化查询）
3. Vector Search（Milvus 向量检索）
4. Keyword Search（BM25 全文检索）
5. Hybrid Merge（混合排序）
6. Graph/LightRAG 检索
7. LLM Generation（大模型生成）
8. 总耗时

输出：分阶段耗时表 + 瓶颈识别 + 优化建议

用法：python scripts/profile_rag.py
"""

import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService
from app.services.graph_service import KnowledgeGraphService
from app.services.lightrag_service import LightRAGService
from app.services.llm_service import LLMService

CHARACTER_ID = 6

TEST_QUESTIONS = [
    "公司的全称是什么？注册地在哪里？",
    "本次发行的保荐机构（主承销商）是哪家？",
    "公司的控股股东和实际控制人是谁？",
    "报告期内公司的营业收入和净利润是多少？",
    "公司的核心竞争力有哪些？",
]

pdf_svc = PDFIngestService()
graph_svc = KnowledgeGraphService()
lightrag_svc = LightRAGService()
llm_svc = LLMService()


class FakeChar:
    name = "招股说明书"
    domain = "金融"
    persona = "你是招股说明书分析师。"
    prompt_template = ""

character = FakeChar()


class Timer:
    """精确计时上下文管理器"""
    def __init__(self, label: str):
        self.label = label
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.elapsed = time.perf_counter() - self._start


def profile_single_query(question: str) -> dict:
    """对单个问题的完整 RAG 流程进行分阶段计时"""
    stages = {}

    # 1. Query Rewriting
    with Timer("query_rewrite") as t:
        memory = ""  # 无历史
        rewritten = llm_svc.rewrite_query(question, memory) if settings.query_rewrite_enabled else question
    stages["query_rewrite"] = t.elapsed

    # 2. Embedding
    with Timer("embedding") as t:
        query_vector = pdf_svc._embed(rewritten)
    stages["embedding"] = t.elapsed

    # 3. Vector Search
    with Timer("vector_search") as t:
        vec_rows = pdf_svc.search_vector(CHARACTER_ID, rewritten)
    stages["vector_search"] = t.elapsed

    # 4. Keyword Search (BM25)
    with Timer("keyword_search") as t:
        kw_rows = pdf_svc.search_keyword(CHARACTER_ID, rewritten)
    stages["keyword_search"] = t.elapsed

    # 5. Hybrid Merge
    with Timer("hybrid_merge") as t:
        hybrid_rows = pdf_svc.search_hybrid(CHARACTER_ID, rewritten)
    stages["hybrid_merge"] = t.elapsed

    # 6. Graph Retrieval
    with Timer("graph_retrieval") as t:
        graph_ctx = graph_svc.graph_context(CHARACTER_ID, rewritten) if graph_svc.has_graph(CHARACTER_ID) else ""
    stages["graph_retrieval"] = t.elapsed

    # 7. LightRAG Retrieval
    with Timer("lightrag_retrieval") as t:
        light_ctx = lightrag_svc.search_dual(CHARACTER_ID, rewritten) if lightrag_svc.has_data(CHARACTER_ID) else ""
    stages["lightrag_retrieval"] = t.elapsed

    # 8. Context Assembly
    with Timer("context_assembly") as t:
        contexts = [str(r.get("text", "")) for r in hybrid_rows]
        context_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
        if graph_ctx:
            context_text += "\n\n" + graph_ctx
    stages["context_assembly"] = t.elapsed

    # 9. LLM Generation
    with Timer("llm_generation") as t:
        answer = llm_svc.generate(character=character, question=question, context=context_text, memory="")
    stages["llm_generation"] = t.elapsed

    stages["total"] = sum(stages.values())
    return stages


def main():
    print("=" * 60)
    print("  RAG 性能瓶颈分析（Task 13）")
    print("=" * 60)
    print(f"\n  模型: {settings.llm_model_name}")
    print(f"  Embedding: {settings.embedding_model_name}")
    print(f"  Milvus: {settings.milvus_url}")
    print(f"  检索模式: {settings.retrieval_mode}")
    print(f"  测试问题数: {len(TEST_QUESTIONS)}\n")

    all_stages: dict[str, list] = {}

    for i, q in enumerate(TEST_QUESTIONS, 1):
        print(f"[{i}/{len(TEST_QUESTIONS)}] {q}")
        stages = profile_single_query(q)
        for k, v in stages.items():
            all_stages.setdefault(k, []).append(v)
        # 打印单题耗时
        bottleneck = max((k, v) for k, v in stages.items() if k != "total")
        print(f"  总耗时: {stages['total']:.2f}s | 瓶颈: {bottleneck[0]} ({bottleneck[1]:.2f}s)")

    # 汇总统计
    print("\n" + "=" * 60)
    print("  分阶段耗时统计（平均值）")
    print("=" * 60)

    stage_names = {
        "query_rewrite": "Query Rewrite (指代消解)",
        "embedding": "Embedding (向量化)",
        "vector_search": "Vector Search (向量检索)",
        "keyword_search": "Keyword Search (BM25)",
        "hybrid_merge": "Hybrid Merge (混合排序)",
        "graph_retrieval": "Graph Retrieval (图检索)",
        "lightrag_retrieval": "LightRAG Retrieval",
        "context_assembly": "Context Assembly (上下文组装)",
        "llm_generation": "LLM Generation (大模型生成)",
        "total": "Total (总计)",
    }

    report_rows = []
    total_avg = sum(sum(v) / len(v) for k, v in all_stages.items() if k != "total")

    for key in ["query_rewrite", "embedding", "vector_search", "keyword_search",
                 "hybrid_merge", "graph_retrieval", "lightrag_retrieval",
                 "context_assembly", "llm_generation", "total"]:
        if key not in all_stages:
            continue
        values = all_stages[key]
        avg = sum(values) / len(values)
        min_v = min(values)
        max_v = max(values)
        pct = (avg / total_avg * 100) if key != "total" and total_avg > 0 else 0
        name = stage_names.get(key, key)
        bar = "█" * int(pct / 2) if key != "total" else ""

        if key == "total":
            print(f"  {'─' * 50}")
        print(f"  {name:<35} {avg:>6.2f}s  (min={min_v:.2f}, max={max_v:.2f})  {pct:>4.0f}%  {bar}")
        report_rows.append({
            "stage": name, "avg": round(avg, 3), "min": round(min_v, 3),
            "max": round(max_v, 3), "pct": round(pct, 1),
        })

    # 识别瓶颈
    non_total = [(k, sum(v)/len(v)) for k, v in all_stages.items() if k != "total"]
    non_total.sort(key=lambda x: -x[1])
    bottleneck_key = non_total[0][0]
    bottleneck_avg = non_total[0][1]
    bottleneck_pct = bottleneck_avg / total_avg * 100

    print(f"\n  🔍 主要瓶颈: {stage_names.get(bottleneck_key, bottleneck_key)}")
    print(f"     占比: {bottleneck_pct:.0f}% | 平均耗时: {bottleneck_avg:.2f}s")

    # 优化建议
    suggestions = {
        "llm_generation": [
            "部署本地 vLLM/SGLang 推理服务减少网络延迟",
            "使用更小的模型（如 DeepSeek-V2-Lite）降低生成时间",
            "启用 KV Cache 复用加速多轮对话",
            "减少 max_tokens 限制不必要的长回复",
        ],
        "embedding": [
            "部署本地 Embedding 服务（如 TEI）替代远程 API",
            "增大 Embedding LRU 缓存容量（当前 512）",
            "使用更轻量的 Embedding 模型（如 bge-small-zh）",
        ],
        "vector_search": [
            "为 Milvus 创建 IVF_FLAT 索引替代暴力搜索",
            "减少 retrieval_top_k 数量",
            "使用 Milvus 分区键减少扫描范围",
        ],
        "keyword_search": [
            "对文档预建倒排索引避免全量扫描",
            "使用 Elasticsearch 替代内存 BM25",
            "缓存 BM25 分词结果",
        ],
        "query_rewrite": [
            "首轮对话跳过 rewrite（无需指代消解）",
            "使用更轻量的模型做 rewrite",
            "缓存相似 query 的 rewrite 结果",
        ],
    }

    print("\n  📋 优化建议:")
    for key, avg_time in non_total[:3]:
        name = stage_names.get(key, key)
        tips = suggestions.get(key, ["暂无具体建议"])
        print(f"\n  [{name}] ({avg_time:.2f}s)")
        for tip in tips:
            print(f"    • {tip}")

    # 生成 Markdown 报告
    lines = [
        "# RAG 性能瓶颈分析报告（Task 13）",
        "",
        f"- **测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **LLM 模型**: {settings.llm_model_name}",
        f"- **Embedding 模型**: {settings.embedding_model_name}",
        f"- **Milvus**: {settings.milvus_url}",
        f"- **测试问题数**: {len(TEST_QUESTIONS)}",
        "",
        "## 分阶段耗时", "",
        "| 阶段 | 平均耗时 | 最小 | 最大 | 占比 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in report_rows:
        lines.append(f"| {r['stage']} | {r['avg']:.3f}s | {r['min']:.3f}s | {r['max']:.3f}s | {r['pct']:.1f}% |")

    lines += [
        "",
        f"## 瓶颈识别",
        "",
        f"**主要瓶颈**: {stage_names.get(bottleneck_key, bottleneck_key)}",
        f"- 占总耗时: **{bottleneck_pct:.0f}%**",
        f"- 平均耗时: **{bottleneck_avg:.2f}s**",
        "",
        "## 优化建议", "",
    ]
    for key, avg_time in non_total[:3]:
        name = stage_names.get(key, key)
        tips = suggestions.get(key, ["暂无具体建议"])
        lines.append(f"### {name} ({avg_time:.2f}s)")
        for tip in tips:
            lines.append(f"- {tip}")
        lines.append("")

    report_path = "profile_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  📄 报告已生成: {report_path}")


if __name__ == "__main__":
    main()
