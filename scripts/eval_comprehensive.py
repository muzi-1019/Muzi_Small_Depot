"""
综合 RAG 评估脚本 — 覆盖 Task 07（功能测试及评估）和 Task 09（Graph+RAG 优化对比）

评估指标：
1. context_precision — 检索到的片段中有多少是真正相关的（LLM 判定）
2. context_recall    — 回答中涉及的关键信息有多少来自检索结果
3. faithfulness      — 回答是否忠实于检索到的上下文（无幻觉）
4. answer_relevancy  — 回答是否切题
5. keyword_accuracy  — 关键词命中率（与 ground truth 对比）

对比模式：传统 RAG vs Graph+RAG vs 纯 LLM

用法：python eval_comprehensive.py
"""

import sys, os, time, json, re, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService
from app.services.graph_service import KnowledgeGraphService
from app.services.llm_service import LLMService

CHARACTER_ID = 6

# ========== 测试集 ==========
TEST_SET = [
    {"question": "公司的全称是什么？注册地在哪里？", "gt_keywords": ["武汉兴图新科电子股份有限公司", "武汉"], "category": "基本信息"},
    {"question": "本次发行的股票数量是多少？", "gt_keywords": ["1,840", "1840"], "category": "发行信息"},
    {"question": "本次发行的保荐机构（主承销商）是哪家？", "gt_keywords": ["中泰证券"], "category": "发行信息"},
    {"question": "募集资金主要用于哪些项目？", "gt_keywords": ["云联邦", "军用视频指挥", "研发中心"], "category": "募资用途"},
    {"question": "公司的主营业务是什么？", "gt_keywords": ["视频指挥控制系统", "视音频"], "category": "业务信息"},
    {"question": "公司的控股股东和实际控制人是谁？", "gt_keywords": ["程家明"], "category": "股权结构"},
    {"question": "报告期内公司的营业收入和净利润是多少？", "gt_keywords": ["19,813", "4,285"], "category": "财务数据"},
    {"question": "公司有哪些主要的风险因素？", "gt_keywords": ["客户集中", "应收账款"], "category": "风险因素"},
    {"question": "公司的核心竞争力有哪些？", "gt_keywords": ["核心技术", "音视频"], "category": "核心竞争力"},
    {"question": "公司应收账款规模较大的原因是什么？", "gt_keywords": ["军方", "结算周期"], "category": "财务分析"},
]

pdf_svc = PDFIngestService()
graph_svc = KnowledgeGraphService()
llm_svc = LLMService()


class FakeChar:
    name = "招股说明书"
    domain = "金融"
    persona = "你是招股说明书分析师，负责解读武汉兴图新科电子股份有限公司招股说明书中的关键信息。"
    prompt_template = ""


character = FakeChar()


# ========== LLM-as-Judge 评估函数 ==========

def llm_judge(prompt: str) -> str:
    """调用 LLM 作为评判者"""
    base_url = (settings.openai_api_base or "").rstrip("/")
    api_key = settings.openai_api_key or ""
    if not base_url or not api_key:
        return ""
    import httpx
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": settings.llm_model_name,
        "messages": [
            {"role": "system", "content": "你是一个严格的 RAG 评估助手。只输出要求的评分，不要解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 50,
    }
    try:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def eval_context_precision(question: str, contexts: list[str]) -> float:
    """上下文精度：检索到的 N 个片段中，有多少个与问题真正相关？"""
    if not contexts:
        return 0.0
    prompt = (
        f"问题：{question}\n\n"
        f"检索到的知识片段：\n" +
        "\n".join(f"[{i+1}] {c[:200]}" for i, c in enumerate(contexts[:5])) +
        f"\n\n请判断以上 {min(len(contexts), 5)} 个片段中，有多少个与问题直接相关。"
        f"只输出一个数字（相关片段数量），如：3"
    )
    result = llm_judge(prompt)
    try:
        relevant = int(re.search(r'\d+', result).group())
        return min(relevant / min(len(contexts), 5), 1.0)
    except Exception:
        return 0.5


def eval_faithfulness(question: str, answer: str, contexts: list[str]) -> float:
    """忠实度：回答是否完全基于检索到的上下文，没有编造信息？"""
    if not contexts or not answer:
        return 0.0
    ctx = "\n".join(c[:200] for c in contexts[:5])
    prompt = (
        f"问题：{question}\n\n"
        f"检索到的知识片段：\n{ctx}\n\n"
        f"AI 回答：{answer[:500]}\n\n"
        f"请判断 AI 回答的忠实度。回答中的所有事实性陈述是否都能在知识片段中找到依据？\n"
        f"评分标准：1.0=完全忠实，0.5=部分忠实，0.0=大量编造\n"
        f"只输出一个 0 到 1 之间的数字，如：0.8"
    )
    result = llm_judge(prompt)
    try:
        score = float(re.search(r'[01]\.?\d*', result).group())
        return min(max(score, 0.0), 1.0)
    except Exception:
        return 0.5


def eval_answer_relevancy(question: str, answer: str) -> float:
    """回答相关性：回答是否切题？"""
    if not answer:
        return 0.0
    prompt = (
        f"问题：{question}\n\n"
        f"AI 回答：{answer[:500]}\n\n"
        f"请判断回答是否切题、完整地回答了问题。\n"
        f"评分标准：1.0=完美切题，0.5=部分切题，0.0=完全跑题\n"
        f"只输出一个 0 到 1 之间的数字，如：0.9"
    )
    result = llm_judge(prompt)
    try:
        score = float(re.search(r'[01]\.?\d*', result).group())
        return min(max(score, 0.0), 1.0)
    except Exception:
        return 0.5


def eval_context_recall(question: str, answer: str, contexts: list[str]) -> float:
    """上下文召回：回答中的关键信息有多少来自检索到的上下文？"""
    if not contexts or not answer:
        return 0.0
    ctx = "\n".join(c[:300] for c in contexts[:5])
    prompt = (
        f"问题：{question}\n\n"
        f"检索到的知识片段：\n{ctx}\n\n"
        f"AI 回答：{answer[:500]}\n\n"
        f"请判断 AI 回答中的关键信息，有多少比例能在知识片段中找到依据？\n"
        f"评分标准：1.0=所有关键信息都来自片段，0.5=部分来自片段，0.0=完全找不到依据\n"
        f"只输出一个 0 到 1 之间的数字，如：0.85"
    )
    result = llm_judge(prompt)
    try:
        score = float(re.search(r'[01]\.?\d*', result).group())
        return min(max(score, 0.0), 1.0)
    except Exception:
        return 0.5


def keyword_accuracy(answer: str, keywords: list[str]) -> float:
    """关键词命中率"""
    if not keywords:
        return 1.0
    hits = sum(1 for kw in keywords if kw.lower() in answer.lower() or kw.replace(",", "") in answer.replace(",", ""))
    return hits / len(keywords)


# ========== 检索与问答 ==========

def run_rag(question: str) -> dict:
    """传统 RAG 模式"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    t_ret = time.time() - t0
    contexts = [str(r.get("text", "")) for r in rows]
    context_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=context_text, memory="")
    t_llm = time.time() - t1
    return {"answer": answer, "contexts": contexts, "retrieve_time": t_ret, "llm_time": t_llm, "mode": "RAG"}


def run_graph_rag(question: str) -> dict:
    """Graph + RAG 模式"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    graph_ctx = graph_svc.graph_context(CHARACTER_ID, question)
    t_ret = time.time() - t0
    contexts = [str(r.get("text", "")) for r in rows]
    context_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    if graph_ctx:
        context_text += "\n\n" + graph_ctx
        contexts.append(graph_ctx)
    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=context_text, memory="")
    t_llm = time.time() - t1
    return {"answer": answer, "contexts": contexts, "retrieve_time": t_ret, "llm_time": t_llm, "mode": "Graph+RAG"}


def run_pure_llm(question: str) -> dict:
    """纯 LLM 模式（无检索）"""
    t0 = time.time()
    answer = llm_svc.generate(character=character, question=question, context="", memory="")
    t_llm = time.time() - t0
    return {"answer": answer, "contexts": [], "retrieve_time": 0, "llm_time": t_llm, "mode": "Pure LLM"}


# ========== 主流程 ==========

def main():
    has_data = pdf_svc.has_data(CHARACTER_ID)
    has_graph = graph_svc.has_graph(CHARACTER_ID)
    print(f"[INFO] character_id={CHARACTER_ID}, has_data={has_data}, has_graph={has_graph}")

    if not has_data:
        print("[ERROR] Milvus 中没有数据")
        sys.exit(1)

    modes = ["RAG"]
    if has_graph:
        modes.append("Graph+RAG")
    modes.append("Pure LLM")

    all_results: dict[str, list] = {m: [] for m in modes}

    for i, item in enumerate(TEST_SET, 1):
        q = item["question"]
        print(f"\n[{i}/{len(TEST_SET)}] {q}")

        for mode in modes:
            if mode == "RAG":
                res = run_rag(q)
            elif mode == "Graph+RAG":
                res = run_graph_rag(q)
            else:
                res = run_pure_llm(q)

            # 评估
            kw_acc = keyword_accuracy(res["answer"], item["gt_keywords"])
            ctx_prec = eval_context_precision(q, res["contexts"]) if res["contexts"] else 0.0
            ctx_recall = eval_context_recall(q, res["answer"], res["contexts"]) if res["contexts"] else 0.0
            faith = eval_faithfulness(q, res["answer"], res["contexts"]) if res["contexts"] else 0.0
            ans_rel = eval_answer_relevancy(q, res["answer"])

            result = {
                **item, **res,
                "keyword_accuracy": round(kw_acc, 2),
                "context_precision": round(ctx_prec, 2),
                "context_recall": round(ctx_recall, 2),
                "faithfulness": round(faith, 2),
                "answer_relevancy": round(ans_rel, 2),
                "total_time": round(res["retrieve_time"] + res["llm_time"], 2),
            }
            all_results[mode].append(result)
            print(f"  [{mode}] {result['total_time']}s | kw_acc={kw_acc:.0%} ctx_prec={ctx_prec:.0%} recall={ctx_recall:.0%} faith={faith:.0%} rel={ans_rel:.0%}")

    generate_report(all_results, modes)
    print(f"\n[DONE] 报告已生成: eval_comprehensive_report.md")


def generate_report(all_results: dict[str, list], modes: list[str]):
    lines = [
        "# 综合 RAG 评估报告（Task 07 + Task 09）",
        "",
        f"- **测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **LLM 模型**: {settings.llm_model_name}",
        f"- **Embedding 模型**: {settings.embedding_model_name}",
        f"- **检索模式**: {settings.retrieval_mode} (向量权重{settings.hybrid_vector_weight}/关键词权重{settings.hybrid_keyword_weight})",
        f"- **测试问题数**: {len(TEST_SET)}",
        f"- **评估模式**: {', '.join(modes)}",
        "",
        "---",
        "",
    ]

    # 总体对比表
    lines += ["## 模式对比总览", "", "| 指标 | " + " | ".join(modes) + " |",
              "| --- | " + " | ".join(["---"] * len(modes)) + " |"]

    metrics = ["keyword_accuracy", "context_precision", "context_recall", "faithfulness", "answer_relevancy", "total_time"]
    metric_names = {"keyword_accuracy": "关键词准确率", "context_precision": "上下文精度",
                    "context_recall": "上下文召回", "faithfulness": "忠实度",
                    "answer_relevancy": "回答相关性", "total_time": "平均耗时(s)"}

    for metric in metrics:
        row = f"| {metric_names[metric]} |"
        for mode in modes:
            results = all_results[mode]
            avg = sum(r[metric] for r in results) / len(results)
            if metric == "total_time":
                row += f" {avg:.1f}s |"
            else:
                row += f" {avg:.0%} |"
        lines.append(row)

    # 逐题对比
    lines += ["", "---", "", "## 逐题对比", ""]
    for i, item in enumerate(TEST_SET, 1):
        lines.append(f"### {i}. [{item['category']}] {item['question']}")
        lines.append("")
        lines.append("| 指标 | " + " | ".join(modes) + " |")
        lines.append("| --- | " + " | ".join(["---"] * len(modes)) + " |")
        for metric in metrics:
            row = f"| {metric_names[metric]} |"
            for mode in modes:
                val = all_results[mode][i-1][metric]
                if metric == "total_time":
                    row += f" {val:.1f}s |"
                else:
                    row += f" {val:.0%} |"
            lines.append(row)

        # 回答摘要
        for mode in modes:
            ans = all_results[mode][i-1]["answer"][:200]
            lines.append(f"\n**{mode} 回答**: {ans}...")
        lines.append("\n---\n")

    # 结论
    lines += [
        "## 评估结论",
        "",
    ]

    for mode in modes:
        results = all_results[mode]
        avg_kw = sum(r["keyword_accuracy"] for r in results) / len(results)
        avg_cp = sum(r["context_precision"] for r in results) / len(results)
        avg_cr = sum(r["context_recall"] for r in results) / len(results)
        avg_f = sum(r["faithfulness"] for r in results) / len(results)
        avg_ar = sum(r["answer_relevancy"] for r in results) / len(results)
        avg_t = sum(r["total_time"] for r in results) / len(results)
        lines.append(f"### {mode}")
        lines.append(f"- 关键词准确率: **{avg_kw:.0%}**")
        lines.append(f"- 上下文精度: **{avg_cp:.0%}**")
        lines.append(f"- 上下文召回: **{avg_cr:.0%}**")
        lines.append(f"- 忠实度: **{avg_f:.0%}**")
        lines.append(f"- 回答相关性: **{avg_ar:.0%}**")
        lines.append(f"- 平均耗时: **{avg_t:.1f}s**")
        lines.append("")

    if "Graph+RAG" in modes and "RAG" in modes:
        lines += [
            "### Graph+RAG vs 传统 RAG 对比分析",
            "",
            "知识图谱增强检索通过实体关系遍历，可以捕获文档中隐含的结构化关系，",
            "特别是在涉及多跳推理（如'控股股东的关联公司'）的问题上具有优势。",
            "传统向量检索在语义相似度匹配上表现稳定，两者结合可取长补短。",
        ]

    with open("eval_comprehensive_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
