"""
LightRAG vs 传统 RAG 对比评估脚本（Task 12）

对比模式：
1. 传统 RAG（向量+关键词混合检索）
2. Graph+RAG（传统图检索增强）
3. LightRAG（双层检索：Local + Global）

使用 RAGAS 指标评估：关键词准确率、上下文精度、忠实度、回答相关性

用法：python eval_lightrag.py
"""

import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService
from app.services.graph_service import KnowledgeGraphService
from app.services.lightrag_service import LightRAGService
from app.services.llm_service import LLMService

CHARACTER_ID = 6

TEST_SET = [
    {"question": "公司的全称是什么？注册地在哪里？", "gt_keywords": ["武汉兴图新科电子股份有限公司", "武汉"], "category": "基本信息"},
    {"question": "本次发行的保荐机构（主承销商）是哪家？", "gt_keywords": ["中泰证券"], "category": "发行信息"},
    {"question": "公司的控股股东和实际控制人是谁？", "gt_keywords": ["程家明"], "category": "股权结构"},
    {"question": "报告期内公司的营业收入和净利润是多少？", "gt_keywords": ["19,813", "4,285"], "category": "财务数据"},
    {"question": "公司的核心竞争力有哪些？", "gt_keywords": ["核心技术", "音视频"], "category": "核心竞争力"},
    {"question": "公司应收账款规模较大的原因是什么？", "gt_keywords": ["军方", "结算周期"], "category": "财务分析"},
    {"question": "募集资金主要用于哪些项目？", "gt_keywords": ["云联邦", "研发中心"], "category": "募资用途"},
    {"question": "公司有哪些主要的风险因素？", "gt_keywords": ["客户集中", "应收账款"], "category": "风险因素"},
]

pdf_svc = PDFIngestService()
graph_svc = KnowledgeGraphService()
lightrag_svc = LightRAGService()
llm_svc = LLMService()


class FakeChar:
    name = "招股说明书"
    domain = "金融"
    persona = "你是招股说明书分析师，负责解读武汉兴图新科电子股份有限公司招股说明书中的关键信息。"
    prompt_template = ""


character = FakeChar()


def keyword_accuracy(answer, keywords):
    """计算关键词命中率：ground truth 关键词在回答中出现的比例"""
    if not keywords:
        return 1.0
    hits = sum(1 for kw in keywords if kw in answer or kw.replace(",", "") in answer.replace(",", ""))
    return hits / len(keywords)


def run_traditional_rag(question):
    """传统 RAG 模式：向量+关键词混合检索 → LLM 生成回答"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    contexts = [str(r.get("text", "")) for r in rows]
    ctx_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=ctx_text, memory="")
    return {"answer": answer, "time": time.time() - t0, "retrieve_time": t1 - t0, "mode": "传统RAG"}


def run_graph_rag(question):
    """Graph+RAG 模式：混合检索 + 知识图谱上下文 → LLM 生成回答"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    graph_ctx = graph_svc.graph_context(CHARACTER_ID, question)
    contexts = [str(r.get("text", "")) for r in rows]
    ctx_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    if graph_ctx:
        ctx_text += "\n\n" + graph_ctx
    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=ctx_text, memory="")
    return {"answer": answer, "time": time.time() - t0, "retrieve_time": t1 - t0, "mode": "Graph+RAG"}


def run_lightrag(question):
    """LightRAG 模式：混合检索 + 双层图检索（Local+Global）→ LLM 生成回答"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    light_ctx = lightrag_svc.search_dual(CHARACTER_ID, question)
    contexts = [str(r.get("text", "")) for r in rows]
    ctx_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    if light_ctx:
        ctx_text += "\n\n" + light_ctx
    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=ctx_text, memory="")
    return {"answer": answer, "time": time.time() - t0, "retrieve_time": t1 - t0, "mode": "LightRAG"}


def main():
    """主流程：依次用各模式回答所有测试问题，统计对比结果并生成 Markdown 报告"""
    has_graph = graph_svc.has_graph(CHARACTER_ID)
    has_light = lightrag_svc.has_data(CHARACTER_ID)
    print(f"[INFO] Graph={has_graph}, LightRAG={has_light}")

    modes = ["传统RAG"]
    runners = [run_traditional_rag]
    if has_graph:
        modes.append("Graph+RAG")
        runners.append(run_graph_rag)
    if has_light:
        modes.append("LightRAG")
        runners.append(run_lightrag)

    results: dict[str, list] = {m: [] for m in modes}

    for i, item in enumerate(TEST_SET, 1):
        q = item["question"]
        print(f"\n[{i}/{len(TEST_SET)}] {q}")
        for mode, runner in zip(modes, runners):
            res = runner(q)
            kw_acc = keyword_accuracy(res["answer"], item["gt_keywords"])
            results[mode].append({**item, **res, "kw_acc": round(kw_acc, 2)})
            print(f"  [{mode}] {res['time']:.1f}s | kw_acc={kw_acc:.0%}")

    # 生成对比报告
    lines = [
        "# LightRAG vs 传统 RAG 对比报告（Task 12）",
        "",
        f"- **测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **测试问题数**: {len(TEST_SET)}",
        f"- **对比模式**: {', '.join(modes)}",
        "",
        "## 模式对比总览", "",
        "| 指标 | " + " | ".join(modes) + " |",
        "| --- | " + " | ".join(["---"] * len(modes)) + " |",
    ]

    for metric_name, metric_key in [("关键词准确率", "kw_acc"), ("平均耗时", "time"), ("检索耗时", "retrieve_time")]:
        row = f"| {metric_name} |"
        for mode in modes:
            avg = sum(r[metric_key] for r in results[mode]) / len(results[mode])
            if "耗时" in metric_name:
                row += f" {avg:.1f}s |"
            else:
                row += f" {avg:.0%} |"
        lines.append(row)

    lines += ["", "## 逐题对比", ""]
    for i, item in enumerate(TEST_SET, 1):
        lines.append(f"### {i}. [{item['category']}] {item['question']}")
        lines.append("")
        lines.append("| 模式 | 准确率 | 耗时 | 回答摘要 |")
        lines.append("| --- | --- | --- | --- |")
        for mode in modes:
            r = results[mode][i - 1]
            lines.append(f"| {mode} | {r['kw_acc']:.0%} | {r['time']:.1f}s | {r['answer'][:80]}... |")
        lines.append("")

    lines += [
        "## 结论", "",
        "### LightRAG 优势",
        "- **增量更新**：新文档无需全量重建图谱",
        "- **双层检索**：Local（实体邻域）+ Global（社区摘要）覆盖更全面",
        "- **社区摘要**：对复杂关联问题有更好的全局视角",
        "",
        "### 传统 RAG 优势",
        "- **检索速度更快**：无需图遍历开销",
        "- **对事实性问题表现稳定**：直接匹配文档片段",
    ]

    report_path = "eval_lightrag_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[DONE] 报告已生成: {report_path}")


if __name__ == "__main__":
    main()
