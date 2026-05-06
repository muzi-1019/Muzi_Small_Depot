"""
纯检索 QPS 压测脚本 — 绕过 LLM，只测 RAG 检索吞吐能力。
用法: python tests/qps_retrieval.py
输出: 检索阶段 QPS、平均/中位/P95 延迟
"""

import sys, os, time, asyncio, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.pdf_ingest_service import PDFIngestService

# ========== 配置 ==========
CHARACTER_ID = 6
CONCURRENT = 50          # 并发数
TOTAL = 200              # 总请求数
QUESTIONS = [
    "公司的主营业务是什么？",
    "控股股东是谁？",
    "本次发行保荐机构是哪家？",
    "募集资金用途是什么？",
    "公司核心竞争力有哪些？",
    "应收账款规模大的原因是什么？",
    "营业收入和净利润是多少？",
    "公司全称是什么？",
    "股权结构是怎样的？",
    "主要风险因素有哪些？",
]

pdf_svc = PDFIngestService()


async def one_search(idx: int) -> dict:
    """执行一次检索，返回耗时"""
    question = QUESTIONS[idx % len(QUESTIONS)]
    t0 = time.perf_counter()
    try:
        rows = pdf_svc.search_hybrid(CHARACTER_ID, question)
        latency = time.perf_counter() - t0
        return {"idx": idx, "ok": True, "latency": latency, "chunks": len(rows)}
    except Exception as e:
        latency = time.perf_counter() - t0
        return {"idx": idx, "ok": False, "latency": latency, "error": str(e)}


async def run_benchmark() -> None:
    semaphore = asyncio.Semaphore(CONCURRENT)

    async def bounded_search(idx: int) -> dict:
        async with semaphore:
            return await one_search(idx)

    print(f"🚀 纯检索 QPS 压测: {TOTAL} 请求, {CONCURRENT} 并发")
    print(f"   问题数: {len(QUESTIONS)}, 角色: character_id={CHARACTER_ID}")
    print()

    t_start = time.perf_counter()
    tasks = [bounded_search(i) for i in range(TOTAL)]
    results = await asyncio.gather(*tasks)
    total_time = time.perf_counter() - t_start

    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]
    latencies = [r["latency"] for r in ok_results]

    success = len(ok_results)
    failed = len(fail_results)
    qps = success / total_time if total_time > 0 else 0

    print("=" * 60)
    print("📊 纯检索压测结果")
    print("=" * 60)
    print(f"  总请求数 : {TOTAL}")
    print(f"  成功数   : {success}")
    print(f"  失败数   : {failed}")
    print(f"  成功率   : {success/TOTAL*100:.1f}%")
    print(f"  总耗时   : {total_time:.2f}s")
    print(f"  QPS      : {qps:.2f}")
    if latencies:
        latencies.sort()
        print(f"  平均延迟 : {statistics.mean(latencies):.3f}s")
        print(f"  中位延迟 : {statistics.median(latencies):.3f}s")
        print(f"  P95 延迟 : {latencies[int(len(latencies)*0.95)]:.3f}s")
        print(f"  P99 延迟 : {latencies[int(len(latencies)*0.99)]:.3f}s")
        print(f"  最大延迟 : {max(latencies):.3f}s")
        avg_chunks = sum(r["chunks"] for r in ok_results) / len(ok_results)
        print(f"  平均片段 : {avg_chunks:.1f} 条/请求")
    if fail_results:
        print()
        print("❌ 失败样例:")
        for r in fail_results[:3]:
            print(f"  #{r['idx']}: {r['error'][:80]}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
