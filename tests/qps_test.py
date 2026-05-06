"""
QPS 压测脚本 — 测试聊天流接口的并发吞吐能力。
用法: python tests/qps_test.py
输出: 总请求数、成功率、平均/中位/95% 响应时间、QPS
"""

import sys, os, time, asyncio, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# ========== 配置 ==========
BASE_URL = "http://127.0.0.1:8000"
USER_ID = 35             # 测试用户 ID（testuser）
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzNSIsImFjY291bnQiOiJ0ZXN0dXNlciIsImV4cCI6MTc3ODEyMzAyN30.r1Fl4JIXiJRYDkyea9vkMIBLmihHMfd2hQGaSTypHts"
CHARACTER_ID = 6         # 招股说明书角色
CONCURRENT = 10          # 并发数
TOTAL = 50               # 总请求数
QUESTION = "公司的主营业务是什么？"
TIMEOUT = 60.0           # 单请求超时（秒）


async def one_request(client: httpx.AsyncClient, idx: int) -> dict:
    """发送一次聊天请求，返回耗时和状态"""
    body = {
        "user_id": USER_ID,
        "character_id": CHARACTER_ID,
        "question": QUESTION,
        "conversation_id": 0,
        "latitude": None,
        "longitude": None,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
    t0 = time.perf_counter()
    try:
        resp = await client.post(f"{BASE_URL}/api/chat/background", headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        latency = time.perf_counter() - t0
        return {"idx": idx, "ok": True, "latency": latency, "status": resp.status_code}
    except Exception as e:
        latency = time.perf_counter() - t0
        return {"idx": idx, "ok": False, "latency": latency, "error": str(e)}


async def run_benchmark() -> None:
    limits = httpx.Limits(max_connections=CONCURRENT * 2)
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(TIMEOUT)) as client:
        semaphore = asyncio.Semaphore(CONCURRENT)

        async def bounded_request(idx: int) -> dict:
            async with semaphore:
                return await one_request(client, idx)

        print(f"🚀 开始压测: {TOTAL} 请求, {CONCURRENT} 并发")
        print(f"   目标: {BASE_URL}/api/chat")
        print(f"   问题: {QUESTION}")
        print()

        t_start = time.perf_counter()
        tasks = [bounded_request(i) for i in range(TOTAL)]
        results = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t_start

    # 统计
    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]
    latencies = [r["latency"] for r in ok_results]

    success = len(ok_results)
    failed = len(fail_results)
    qps = success / total_time if total_time > 0 else 0

    print("=" * 60)
    print("📊 压测结果")
    print("=" * 60)
    print(f"  总请求数 : {TOTAL}")
    print(f"  成功数   : {success}")
    print(f"  失败数   : {failed}")
    print(f"  成功率   : {success/TOTAL*100:.1f}%")
    print(f"  总耗时   : {total_time:.2f}s")
    print(f"  QPS      : {qps:.2f}")
    if latencies:
        latencies.sort()
        print(f"  平均延迟 : {statistics.mean(latencies):.2f}s")
        print(f"  中位延迟 : {statistics.median(latencies):.2f}s")
        print(f"  P95 延迟 : {latencies[int(len(latencies)*0.95)]:.2f}s")
        print(f"  最大延迟 : {max(latencies):.2f}s")
    if fail_results:
        print()
        print("❌ 失败样例:")
        for r in fail_results[:3]:
            print(f"  #{r['idx']}: {r['error'][:80]}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
