# -*- coding: utf-8 -*-
"""
RAG Retrieval Quality Evaluator
评估角色扮演系统中向量检索模块的召回质量与排序效果。
支持指标：Recall@K、Precision@K、MRR、NDCG@K、Average Precision。
"""

import argparse
import json
import logging
import math
import statistics
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class EvalExample:
    """单条评估样本"""
    query: str
    relevant_ids: List[str]
    character_id: int = 0


@dataclass
class RetrievalResult:
    """检索结果"""
    retrieved_ids: List[str]
    scores: List[float] = field(default_factory=list)


class MetricsCalculator:
    """检索评估指标计算器"""

    @staticmethod
    def recall_at_k(relevant: List[str], retrieved: List[str], k: int) -> float:
        if not relevant:
            return 0.0
        retrieved_k = retrieved[:k]
        hits = len(set(relevant) & set(retrieved_k))
        return hits / len(relevant)

    @staticmethod
    def precision_at_k(relevant: List[str], retrieved: List[str], k: int) -> float:
        if k <= 0:
            return 0.0
        retrieved_k = retrieved[:k]
        if not retrieved_k:
            return 0.0
        hits = len(set(relevant) & set(retrieved_k))
        return hits / k

    @staticmethod
    def mrr(relevant: List[str], retrieved: List[str]) -> float:
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                return 1.0 / rank
        return 0.0

    @staticmethod
    def dcg_at_k(relevances: List[float], k: int) -> float:
        dcg = 0.0
        for i, rel in enumerate(relevances[:k], start=1):
            dcg += rel / math.log2(i + 1)
        return dcg

    def ndcg_at_k(self, relevant: List[str], retrieved: List[str], k: int) -> float:
        relevances = [1.0 if doc_id in relevant else 0.0 for doc_id in retrieved[:k]]
        ideal_relevances = sorted([1.0] * len(relevant) + [0.0] * max(0, k - len(relevant)), reverse=True)[:k]
        actual_dcg = self.dcg_at_k(relevances, k)
        ideal_dcg = self.dcg_at_k(ideal_relevances, k)
        if ideal_dcg == 0.0:
            return 0.0
        return actual_dcg / ideal_dcg

    def average_precision(self, relevant: List[str], retrieved: List[str]) -> float:
        if not relevant:
            return 0.0
        precisions = []
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                precisions.append(self.precision_at_k(relevant, retrieved, rank))
        if not precisions:
            return 0.0
        return sum(precisions) / len(relevant)


class SimpleVectorSearcher:
    """简易向量检索模拟器，用于无外部依赖演示；实际环境可替换为 Milvus/ES 客户端。"""

    def __init__(self, collection_name: str = "character_knowledge"):
        self.collection_name = collection_name
        self._docs: Dict[str, str] = {}

    def index_document(self, doc_id: str, content: str) -> None:
        self._docs[doc_id] = content.lower()

    def search(self, query: str, top_k: int = 10) -> RetrievalResult:
        q = query.lower()
        scored = []
        for doc_id, content in self._docs.items():
            score = 0.0
            query_terms = q.split()
            for term in query_terms:
                if term in content:
                    score += 1.0
            if score > 0.0:
                scored.append((doc_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]
        return RetrievalResult(
            retrieved_ids=[doc_id for doc_id, _ in top],
            scores=[score for _, score in top],
        )


class RAGEvaluator:
    """RAG 检索质量评估器主控类"""

    def __init__(self, searcher: SimpleVectorSearcher, calculator: MetricsCalculator):
        self.searcher = searcher
        self.calculator = calculator
        self.results: List[Dict] = []

    def evaluate_example(self, example: EvalExample, k_values: List[int] = None) -> Dict:
        if k_values is None:
            k_values = [1, 3, 5, 10]
        retrieval = self.searcher.search(example.query, top_k=max(k_values))
        metrics = {"query": example.query, "character_id": example.character_id}
        for k in k_values:
            metrics[f"recall@{k}"] = self.calculator.recall_at_k(example.relevant_ids, retrieval.retrieved_ids, k)
            metrics[f"precision@{k}"] = self.calculator.precision_at_k(example.relevant_ids, retrieval.retrieved_ids, k)
            metrics[f"ndcg@{k}"] = self.calculator.ndcg_at_k(example.relevant_ids, retrieval.retrieved_ids, k)
        metrics["mrr"] = self.calculator.mrr(example.relevant_ids, retrieval.retrieved_ids)
        metrics["map"] = self.calculator.average_precision(example.relevant_ids, retrieval.retrieved_ids)
        metrics["retrieved"] = retrieval.retrieved_ids
        return metrics

    def run(self, examples: List[EvalExample], k_values: Optional[List[int]] = None) -> Dict:
        self.results = []
        for ex in examples:
            result = self.evaluate_example(ex, k_values)
            self.results.append(result)
            logger.info("Evaluated query: %s | MAP=%.4f", ex.query[:40], result["map"])
        return self.aggregate(k_values)

    def aggregate(self, k_values: Optional[List[int]] = None) -> Dict:
        if k_values is None:
            k_values = [1, 3, 5, 10]
        if not self.results:
            return {}
        summary: Dict[str, List[float]] = {}
        for key in self.results[0]:
            if key.startswith(("recall", "precision", "ndcg", "mrr", "map")):
                summary[key] = [r[key] for r in self.results]
        aggregated = {}
        for key, values in summary.items():
            aggregated[f"{key}_mean"] = statistics.mean(values)
            aggregated[f"{key}_std"] = statistics.stdev(values) if len(values) > 1 else 0.0
        aggregated["num_queries"] = len(self.results)
        return aggregated

    def export_report(self, output_path: str) -> None:
        report = {
            "per_query": self.results,
            "summary": self.aggregate(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Report saved to %s", output_path)


def build_demo_corpus() -> SimpleVectorSearcher:
    searcher = SimpleVectorSearcher()
    corpus = {
        "doc_001": "本公司主营业务涵盖人工智能技术研发与咨询服务，专注于大语言模型和向量数据库领域。",
        "doc_002": "公司成立于2018年，总部位于北京，核心团队来自国内外顶尖高校与科技企业。",
        "doc_003": "主要产品包括智能客服系统、知识管理平台以及RAG检索增强生成解决方案。",
        "doc_004": "财务报告显示，2023年度营业收入同比增长35%，净利润率达到18.5%。",
        "doc_005": "研发投入占比连续三年超过20%，已获得多项自然语言处理相关发明专利。",
        "doc_006": "客户群体覆盖金融、电商、教育、医疗等多个行业，累计服务超过500家企业客户。",
        "doc_007": "公司战略目标是成为全球领先的企业级AI基础设施提供商，持续推动行业智能化升级。",
        "doc_008": "本次募投项目主要用于新一代向量数据库研发、大模型微调平台建设以及市场拓展。",
        "doc_009": "风险因素提示：技术迭代风险、市场竞争加剧风险、核心人才流失风险以及政策合规风险。",
        "doc_010": "公司已通过ISO27001信息安全管理体系认证，数据隐私保护措施符合GDPR与国内法规要求。",
    }
    for doc_id, content in corpus.items():
        searcher.index_document(doc_id, content)
    return searcher


def build_demo_examples() -> List[EvalExample]:
    return [
        EvalExample(query="公司主营业务是什么？", relevant_ids=["doc_001", "doc_003"], character_id=1),
        EvalExample(query="公司成立时间和地点？", relevant_ids=["doc_002"], character_id=1),
        EvalExample(query="2023年的财务表现如何？", relevant_ids=["doc_004"], character_id=1),
        EvalExample(query="研发投入占比是多少？", relevant_ids=["doc_005"], character_id=1),
        EvalExample(query="募投项目用途？", relevant_ids=["doc_008"], character_id=1),
        EvalExample(query="公司有哪些风险？", relevant_ids=["doc_009"], character_id=1),
        EvalExample(query="客户覆盖哪些行业？", relevant_ids=["doc_006"], character_id=1),
        EvalExample(query="公司战略目标是什么？", relevant_ids=["doc_007"], character_id=1),
        EvalExample(query="数据安全认证有哪些？", relevant_ids=["doc_010"], character_id=1),
        EvalExample(query="核心产品和业务方向", relevant_ids=["doc_001", "doc_003"], character_id=1),
    ]


def print_summary(summary: Dict) -> None:
    print("\n" + "=" * 50)
    print("RAG Retrieval Evaluation Summary")
    print("=" * 50)
    print(f"Queries evaluated: {summary.get('num_queries', 0)}")
    for key in sorted(summary.keys()):
        if key.endswith("_mean"):
            metric = key.replace("_mean", "")
            mean_val = summary[key]
            std_val = summary.get(f"{metric}_std", 0.0)
            print(f"  {metric:<12} mean={mean_val:.4f}  std={std_val:.4f}")
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG Retrieval Quality Evaluator")
    parser.add_argument("--report", type=str, default="rag_eval_report.json", help="Output JSON report path")
    parser.add_argument("--demo", action="store_true", default=True, help="Run with demo corpus")
    args = parser.parse_args()

    if not args.demo:
        logger.warning("Custom corpus mode not implemented in demo; use --demo")
        return 1

    searcher = build_demo_corpus()
    calculator = MetricsCalculator()
    evaluator = RAGEvaluator(searcher, calculator)
    examples = build_demo_examples()

    summary = evaluator.run(examples, k_values=[1, 3, 5, 10])
    print_summary(summary)
    evaluator.export_report(args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
