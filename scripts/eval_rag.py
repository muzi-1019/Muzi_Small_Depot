"""
RAG 检索质量评估脚本
- 10 个通用问题 + 5 个表格专项问题
- 每道题有 ground_truth（标准答案关键词）
- 评估指标：关键词命中率（准确率代理）、检索召回判定、响应时间
- 输出：eval_report.md

用法：python eval_rag.py
"""

import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService
from app.services.llm_service import LLMService

CHARACTER_ID = 6

# ========== 测试集：问题 + ground truth 关键词 ==========
# ground_truth 中的关键词如果在 RAG 回答中出现，则算"命中"
TEST_SET = [
    # ---- 通用问题（10题） ----
    {
        "question": "公司的全称是什么？注册地在哪里？",
        "ground_truth_keywords": ["武汉兴图新科电子股份有限公司", "湖北", "武汉", "东湖新技术开发区"],
        "category": "基本信息",
    },
    {
        "question": "本次发行的股票数量是多少？占发行后总股本的比例是多少？",
        "ground_truth_keywords": ["1,840", "1840", "25%", "25.00"],
        "category": "发行信息",
    },
    {
        "question": "本次发行的保荐机构（主承销商）是哪家？",
        "ground_truth_keywords": ["中泰证券"],
        "category": "发行信息",
    },
    {
        "question": "募集资金主要用于哪些项目？",
        "ground_truth_keywords": ["云联邦", "军用视频指挥", "研发中心", "补充流动资金"],
        "category": "募资用途",
    },
    {
        "question": "公司的主营业务是什么？主要产品有哪些？",
        "ground_truth_keywords": ["视频指挥控制系统", "军队", "视音频"],
        "category": "业务信息",
    },
    {
        "question": "公司的控股股东和实际控制人是谁？",
        "ground_truth_keywords": ["程家明"],
        "category": "股权结构",
    },
    {
        "question": "报告期内公司的营业收入和净利润是多少？",
        "ground_truth_keywords": ["19,813", "19813", "4,285", "4285"],
        "category": "财务数据",
    },
    {
        "question": "公司有哪些主要的风险因素？",
        "ground_truth_keywords": ["客户集中", "应收账款", "涉密", "军改"],
        "category": "风险因素",
    },
    {
        "question": "公司的核心竞争力有哪些？",
        "ground_truth_keywords": ["核心技术", "音视频", "中间件", "专利"],
        "category": "核心竞争力",
    },
    {
        "question": "公司应收账款规模较大的原因是什么？",
        "ground_truth_keywords": ["军方", "结算周期", "预算"],
        "category": "财务分析",
    },
    # ---- 表格专项问题（5题） ----
    {
        "question": "2018年末公司的应收账款余额是多少？",
        "ground_truth_keywords": ["18,597", "应收账款"],
        "category": "表格-财务",
    },
    {
        "question": "公司2016年到2018年的研发费用分别是多少？",
        "ground_truth_keywords": ["研发", "费用"],
        "category": "表格-研发",
    },
    {
        "question": "公司前五大客户的收入占比是多少？",
        "ground_truth_keywords": ["52", "80", "前五"],
        "category": "表格-客户",
    },
    {
        "question": "募集资金投资项目的总投资金额是多少？",
        "ground_truth_keywords": ["20,658", "补充流动资金", "募集资金"],
        "category": "表格-募资",
    },
    {
        "question": "公司的员工总人数和研发人员占比是多少？",
        "ground_truth_keywords": ["研发", "人员", "占比"],
        "category": "表格-人员",
    },
]

pdf_svc = PDFIngestService()
llm_svc = LLMService()

class FakeCharacter:
    def __init__(self):
        self.name = "招股说明书"
        self.domain = "金融"
        self.persona = "你是招股说明书分析师，负责解读武汉兴图新科电子股份有限公司招股说明书中的关键信息。"
        self.prompt_template = ""

character = FakeCharacter()


def evaluate_answer(answer: str, keywords: list[str]) -> dict:
    """评估回答质量：计算关键词命中率"""
    hits = []
    misses = []
    for kw in keywords:
        if kw.lower() in answer.lower() or kw.replace(",", "") in answer.replace(",", ""):
            hits.append(kw)
        else:
            misses.append(kw)
    accuracy = len(hits) / max(len(keywords), 1)
    return {"hits": hits, "misses": misses, "accuracy": accuracy}


def evaluate_recall(contexts: list[str], keywords: list[str]) -> dict:
    """评估检索召回率：ground truth 关键词有多少出现在检索到的上下文片段中（而非最终回答）。
    召回率 = 在检索片段中命中的关键词数 / 全部 ground truth 关键词数
    """
    combined = " ".join(contexts).lower().replace(",", "")
    recalled = []
    missed = []
    for kw in keywords:
        kw_norm = kw.lower().replace(",", "")
        if kw_norm in combined:
            recalled.append(kw)
        else:
            missed.append(kw)
    recall = len(recalled) / max(len(keywords), 1)
    return {"recall": recall, "recalled": recalled, "recall_missed": missed}


def run_single(question: str) -> dict:
    """执行单个问题的 RAG 检索与回答"""
    t0 = time.time()
    rows = pdf_svc.search_with_meta(CHARACTER_ID, question)
    t_retrieve = time.time() - t0

    context_parts = [f"[{i}] {row.get('text', '')}" for i, row in enumerate(rows, 1)]
    context = "\n\n".join(context_parts)

    t1 = time.time()
    answer = llm_svc.generate(character=character, question=question, context=context, memory="")
    t_llm = time.time() - t1

    return {
        "answer": answer,
        "num_chunks": len(rows),
        "retrieve_time": round(t_retrieve, 2),
        "llm_time": round(t_llm, 2),
        "total_time": round(t_retrieve + t_llm, 2),
        "top_scores": [round(float(r.get("hybrid_score", r.get("score", 0))), 4) for r in rows[:3]],
        "contexts": [str(r.get("text", "")) for r in rows],
    }


def main():
    has = pdf_svc.has_data(CHARACTER_ID)
    print(f"[INFO] character_id={CHARACTER_ID}, has_data={has}")
    if not has:
        print("[ERROR] 该角色在 Milvus 中没有数据，请先上传 PDF。")
        sys.exit(1)

    results = []
    total_accuracy = 0
    for i, item in enumerate(TEST_SET, 1):
        q = item["question"]
        print(f"\n[{i}/{len(TEST_SET)}] [{item['category']}] {q}")
        res = run_single(q)
        eval_result = evaluate_answer(res["answer"], item["ground_truth_keywords"])
        recall_result = evaluate_recall(res["contexts"], item["ground_truth_keywords"])
        print(f"  ✓ {res['total_time']}s | 准确率: {eval_result['accuracy']:.0%} | 召回率: {recall_result['recall']:.0%} | 命中: {eval_result['hits']} | 缺失: {eval_result['misses']}")
        total_accuracy += eval_result["accuracy"]
        results.append({
            **item,
            **res,
            **eval_result,
            **recall_result,
        })

    avg_accuracy = total_accuracy / len(TEST_SET)
    avg_recall = sum(r["recall"] for r in results) / len(results)
    print(f"\n[DONE] 平均准确率: {avg_accuracy:.1%} | 平均召回率: {avg_recall:.1%}")

    generate_report(results, avg_accuracy, avg_recall)
    print(f"报告已生成: eval_report.md")


def generate_report(results: list, avg_accuracy: float, avg_recall: float):
    lines = [
        "# RAG 检索质量评估报告",
        "",
        f"- **测试角色**: 招股说明书 (character_id={CHARACTER_ID})",
        f"- **测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **LLM 模型**: {settings.llm_model_name}",
        f"- **Embedding 模型**: {settings.embedding_model_name}",
        f"- **混合检索**: 向量(0.6) + 关键词(0.4) 并行执行",
        f"- **测试问题数**: {len(results)} (通用10 + 表格专项5)",
        f"- **平均准确率**: **{avg_accuracy:.1%}**",
        f"- **平均召回率**: **{avg_recall:.1%}**",
        "",
        "---",
        "",
        "## 总览",
        "",
        "| # | 类别 | 问题 | 准确率 | 召回率 | 耗时 | 检索数 | 命中关键词 | 召回缺失 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    total_time = 0
    for i, r in enumerate(results, 1):
        acc_icon = "✅" if r["accuracy"] >= 0.75 else ("🟡" if r["accuracy"] >= 0.5 else "❌")
        rec_icon = "✅" if r["recall"] >= 0.75 else ("🟡" if r["recall"] >= 0.5 else "❌")
        lines.append(
            f"| {i} | {r['category']} | {r['question'][:18]}… | {acc_icon} {r['accuracy']:.0%} "
            f"| {rec_icon} {r['recall']:.0%} "
            f"| {r['total_time']}s | {r['num_chunks']} "
            f"| {', '.join(r['hits']) if r['hits'] else '-'} "
            f"| {', '.join(r['recall_missed']) if r['recall_missed'] else '-'} |"
        )
        total_time += r["total_time"]

    avg_time = round(total_time / len(results), 2)
    lines.append(f"| **平均** | | | **{avg_accuracy:.0%}** | **{avg_recall:.0%}** | **{avg_time}s** | | | |")

    # 分类统计
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "total_acc": 0.0, "total_rec": 0.0, "total_time": 0.0}
        categories[cat]["count"] += 1
        categories[cat]["total_acc"] += r["accuracy"]
        categories[cat]["total_rec"] += r["recall"]
        categories[cat]["total_time"] += r["total_time"]

    lines += [
        "",
        "---",
        "",
        "## 分类统计",
        "",
        "| 类别 | 题数 | 平均准确率 | 平均召回率 | 平均耗时 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cat, stats in categories.items():
        cat_avg_acc = stats["total_acc"] / stats["count"]
        cat_avg_rec = stats["total_rec"] / stats["count"]
        cat_avg_time = round(stats["total_time"] / stats["count"], 2)
        lines.append(f"| {cat} | {stats['count']} | {cat_avg_acc:.0%} | {cat_avg_rec:.0%} | {cat_avg_time}s |")

    # 详细回答
    lines += ["", "---", "", "## 详细回答", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"### {i}. [{r['category']}] {r['question']}")
        lines.append("")
        lines.append(f"**准确率**: {r['accuracy']:.0%} | **耗时**: {r['total_time']}s (检索{r['retrieve_time']}s + LLM{r['llm_time']}s) | **检索**: {r['num_chunks']}条")
        lines.append("")
        lines.append(f"**命中关键词**: {', '.join(r['hits']) if r['hits'] else '无'}")
        lines.append(f"**缺失关键词**: {', '.join(r['misses']) if r['misses'] else '无'}")
        lines.append("")
        lines.append(f"> {r['answer'][:600]}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 结论
    lines += [
        "## 检索优化分析",
        "",
        "### 当前系统架构",
        "- **混合检索**: 向量检索(COSINE, 权重0.6) + 关键词检索(词项匹配, 权重0.4)",
        "- **并行执行**: ThreadPoolExecutor 同时执行向量和关键词检索",
        "- **Embedding 缓存**: LRU 缓存(512条)减少重复查询开销",
        "- **表格解析**: PyMuPDF find_tables() → Markdown 格式保留结构",
        "- **图像解析**: 多模态视觉模型(Qwen-VL)生成文字描述",
        "",
        "### 性能瓶颈分析",
        f"- **Embedding API 调用**: ~6s（远程 SiliconFlow {settings.embedding_model_name}）",
        f"- **LLM 生成**: ~{round(sum(r['llm_time'] for r in results) / len(results), 1)}s（{settings.llm_model_name}）",
        "- **Milvus 检索**: <0.5s（含网络延迟）",
        "",
        "### 优化建议",
        "1. **本地 Embedding 模型**: 部署本地 bge-large-zh 可将检索耗时从 6s 降至 <0.5s",
        "2. **查询改写**: 已实现多轮对话指代消解(Query Rewriting)，提升多轮场景检索准确率",
        "3. **结果重排**: 可引入 Cross-Encoder 重排序进一步提升精度",
        f"4. **当前准确率**: {avg_accuracy:.0%}，{'已达标(≥90%)' if avg_accuracy >= 0.9 else '需进一步优化检索策略'}",
        f"5. **当前召回率**: {avg_recall:.0%}，{'已达标(≥95%)' if avg_recall >= 0.95 else '需进一步提升召回率'}",
    ]

    with open("eval_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
