"""
RAG vs 纯LLM 对比评测脚本
针对「招股说明书」角色（character_id=6），使用 10 个预设问题分别进行 RAG 检索回答和纯 LLM 回答，
测量响应时间、检索片段数、回答差异，并生成 Markdown 评测报告。

用法：python benchmark_rag.py
输出：benchmark_report.md
"""

import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService
from app.services.llm_service import LLMService

# ========== 配置 ==========
CHARACTER_ID = 6  # 招股说明书
QUESTIONS = [
    "公司的全称是什么？注册地在哪里？",
    "本次发行的股票数量是多少？占发行后总股本的比例是多少？",
    "本次发行的保荐机构（主承销商）是哪家？",
    "募集资金主要用于哪些项目？",
    "公司的主营业务是什么？主要产品有哪些？",
    "公司的控股股东和实际控制人是谁？",
    "报告期内公司的营业收入和净利润是多少？",
    "公司有哪些主要的风险因素？",
    "公司的核心竞争力有哪些？",
    "公司应收账款规模较大的原因是什么？",
]

# ========== 工具函数 ==========
pdf_svc = PDFIngestService()
llm_svc = LLMService()

# 伪造一个 character 对象供 LLM 调用
class FakeCharacter:
    def __init__(self):
        self.name = "招股说明书"
        self.domain = "金融"
        self.persona = "你是招股说明书分析师，负责解读武汉兴图新科电子股份有限公司招股说明书中的关键信息。"
        self.prompt_template = ""

character = FakeCharacter()

def run_with_rag(question: str) -> dict:
    """RAG 模式：检索 + LLM"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    t_retrieve = time.time() - t0

    context_parts = []
    for i, row in enumerate(rows, 1):
        context_parts.append(f"[{i}] {row.get('text', '')}")
    context = "\n\n".join(context_parts)

    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=context, memory="")
    t_llm = time.time() - t1

    sources = [
        {"source_file": r.get("source_file", ""), "score": round(float(r.get("hybrid_score", r.get("score", 0))), 4)}
        for r in rows[:3]
    ]
    return {
        "answer": answer,
        "sources": sources,
        "num_chunks": len(rows),
        "retrieve_time": round(t_retrieve, 2),
        "llm_time": round(t_llm, 2),
        "total_time": round(t_retrieve + t_llm, 2),
    }

def run_without_rag(question: str) -> dict:
    """纯 LLM 模式：无检索"""
    t0 = time.time()
    answer = llm_svc.generate(character=character, question=question, context="", memory="")
    t_llm = time.time() - t0
    return {
        "answer": answer,
        "total_time": round(t_llm, 2),
    }

# ========== 主流程 ==========
def main():
    # 先检查是否有数据
    has = pdf_svc.has_data(CHARACTER_ID)
    print(f"[INFO] character_id={CHARACTER_ID}, has_data={has}")
    if not has:
        print("[ERROR] 该角色在 Milvus 中没有数据，请先上传 PDF。")
        sys.exit(1)

    results = []
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[{i}/{len(QUESTIONS)}] {q}")
        print("  RAG 模式...")
        rag = run_with_rag(q)
        print(f"  ✓ RAG: {rag['total_time']}s, {rag['num_chunks']} chunks")
        print("  纯LLM 模式...")
        llm = run_without_rag(q)
        print(f"  ✓ LLM: {llm['total_time']}s")
        results.append({"question": q, "rag": rag, "llm": llm})

    # 生成报告
    generate_report(results)
    print(f"\n[DONE] 报告已生成: benchmark_report.md")

def generate_report(results: list):
    lines = [
        "# RAG vs 纯LLM 对比评测报告",
        "",
        f"- **测试角色**: 招股说明书 (character_id={CHARACTER_ID})",
        f"- **测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **LLM 模型**: {settings.llm_model_name}",
        f"- **Embedding 模型**: {settings.embedding_model_name}",
        f"- **测试问题数**: {len(results)}",
        "",
        "---",
        "",
        "## 性能对比总览",
        "",
        "| # | 问题 | RAG 耗时 | 纯LLM 耗时 | 检索数 | RAG 更快? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    total_rag_time = 0
    total_llm_time = 0
    for i, r in enumerate(results, 1):
        rag_t = r["rag"]["total_time"]
        llm_t = r["llm"]["total_time"]
        total_rag_time += rag_t
        total_llm_time += llm_t
        faster = "✅" if rag_t <= llm_t else "❌"
        lines.append(f"| {i} | {r['question'][:20]}… | {rag_t}s | {llm_t}s | {r['rag']['num_chunks']} | {faster} |")

    avg_rag = round(total_rag_time / len(results), 2)
    avg_llm = round(total_llm_time / len(results), 2)
    lines.append(f"| **平均** | | **{avg_rag}s** | **{avg_llm}s** | | |")

    lines += [
        "",
        "---",
        "",
        "## 详细对比",
        "",
    ]

    for i, r in enumerate(results, 1):
        lines.append(f"### 问题 {i}: {r['question']}")
        lines.append("")
        lines.append(f"**RAG 回答** (耗时 {r['rag']['total_time']}s, 检索 {r['rag']['num_chunks']} 条, 检索耗时 {r['rag']['retrieve_time']}s, LLM 耗时 {r['rag']['llm_time']}s):")
        lines.append("")
        lines.append(f"> {r['rag']['answer'][:500]}")
        lines.append("")
        if r["rag"]["sources"]:
            lines.append("参考来源:")
            for s in r["rag"]["sources"]:
                lines.append(f"- {s['source_file']} (相似度: {s['score']})")
        lines.append("")
        lines.append(f"**纯LLM 回答** (耗时 {r['llm']['total_time']}s):")
        lines.append("")
        lines.append(f"> {r['llm']['answer'][:500]}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines += [
        "## 结论",
        "",
        f"- RAG 平均响应时间: **{avg_rag}s**（含检索 + LLM）",
        f"- 纯LLM 平均响应时间: **{avg_llm}s**",
        f"- RAG 模式通过向量知识库检索提供了基于文档的事实性回答",
        f"- 纯LLM 模式依赖模型自身知识，可能产生幻觉或缺乏具体数据",
    ]

    with open("benchmark_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    main()
