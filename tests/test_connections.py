"""
全链路连通性测试脚本。
一键检测项目运行所依赖的所有外部服务是否可以正常连通，包括：
1. MySQL 数据库 —— 用户/会话/角色等持久化数据存储
2. Redis 缓存 —— 短期对话记忆、并发控制
3. Milvus 向量数据库 —— PDF 知识片段的向量检索
4. LLM API（大语言模型）—— DeepSeek 等模型的文本生成
5. Embedding API —— 文本向量化（bge-large-zh 等）
6. FastAPI 后端服务 —— 本地 Web 服务是否已启动

用法：python test_connections.py
输出：逐项显示连接状态（✅ / ❌），最终汇总结果。
"""

import sys
import os
import time

# Windows 终端默认 GBK 编码，设置为 UTF-8 以支持中文和 emoji 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保项目根目录在 Python 模块搜索路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


# ==================== 辅助函数 ====================

def test_with_timeout(test_name: str, test_func, timeout_hint: str = ""):
    """
    执行单个连通性测试，捕获异常并格式化输出结果。
    参数：
        test_name: 测试项名称（显示用）
        test_func: 实际执行测试的函数，返回描述字符串
        timeout_hint: 超时提示信息
    返回：
        (bool, str) — 是否通过、详情描述
    """
    print(f"\n{'='*60}")
    print(f"🔍 测试: {test_name}")
    if timeout_hint:
        print(f"   提示: {timeout_hint}")
    print("-" * 60)
    t0 = time.time()
    try:
        detail = test_func()
        elapsed = time.time() - t0
        print(f"   ✅ 通过 ({elapsed:.2f}s)")
        print(f"   详情: {detail}")
        return True, detail
    except Exception as e:
        elapsed = time.time() - t0
        print(f"   ❌ 失败 ({elapsed:.2f}s)")
        print(f"   错误: {e}")
        return False, str(e)


# ==================== 各项测试函数 ====================

def test_mysql():
    """测试 MySQL 连接：尝试执行 SELECT 1 查询"""
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.mysql_dsn, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        row = result.fetchone()
        assert row[0] == 1

    # 检查表是否存在
    with engine.connect() as conn:
        result = conn.execute(text("SHOW TABLES"))
        tables = [r[0] for r in result.fetchall()]

    engine.dispose()
    return f"连接成功 | DSN: {settings.mysql_dsn.split('@')[-1]} | 已有 {len(tables)} 张表: {', '.join(tables[:10])}"


def test_redis():
    """测试 Redis 连接：PING + 写入/读取一个临时 key"""
    import redis

    r = redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    # 基础连通性
    pong = r.ping()
    assert pong, "Redis PING 未返回 True"

    # 读写测试
    test_key = "__connectivity_test__"
    r.set(test_key, "ok", ex=10)  # 写入，10秒过期
    val = r.get(test_key)
    r.delete(test_key)
    assert val == b"ok", f"Redis 读写不一致: {val}"

    # 获取 Redis 信息
    info = r.info("server")
    version = info.get("redis_version", "unknown")
    db_size = r.dbsize()
    r.close()
    return f"PING=OK | 读写测试通过 | 版本: {version} | 当前 key 数: {db_size} | URL: {settings.redis_url}"


def test_milvus():
    """测试 Milvus 向量数据库连接：连接 + 列出集合 + 查询数据量"""
    from pymilvus import connections, utility, Collection

    # 连接 Milvus
    connections.connect(alias="__test__", uri=settings.milvus_url, db_name=settings.milvus_db, timeout=10)

    # 列出所有集合
    collections = utility.list_collections(using="__test__")

    # 检查角色知识库集合（每个角色独立集合：character_knowledge_{id}）
    prefix = settings.milvus_collection
    detail_parts = [f"URI: {settings.milvus_url}", f"集合数: {len(collections)}"]

    char_collections = [c for c in collections if c.startswith(prefix + "_")]
    if char_collections:
        for cname in char_collections:
            coll = Collection(cname, using="__test__")
            num_entities = coll.num_entities
            detail_parts.append(f"'{cname}': {num_entities} 条记录")
    else:
        detail_parts.append(f"⚠️ 未找到 '{prefix}_*' 角色知识库集合（首次使用需先上传 PDF）")

    connections.disconnect("__test__")
    return " | ".join(detail_parts)


def test_llm_api():
    """测试 LLM API 连通性：发送一个极简的 chat completion 请求"""
    if settings.llm_provider == "mock":
        return f"当前为 mock 模式（llm_provider=mock），跳过实际 API 调用"

    import httpx

    base_url = (settings.openai_api_base or "").rstrip("/")
    api_key = settings.openai_api_key or ""

    if not base_url or not api_key:
        return "⚠️ 未配置 LLM API (openai_api_base / openai_api_key 为空)"

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.llm_model_name,
        "messages": [{"role": "user", "content": "请回复OK"}],
        "max_tokens": 10,
    }

    with httpx.Client(timeout=15.0, trust_env=False) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    model_used = data.get("model", settings.llm_model_name)
    return f"API 响应正常 | 模型: {model_used} | 回复: '{reply[:50]}' | 端点: {base_url}"


def test_embedding_api():
    """测试 Embedding API 连通性：发送一条短文本进行向量化"""
    base_url = (settings.openai_api_base or "").rstrip("/")
    api_key = settings.openai_api_key or ""

    if not base_url or not api_key:
        return "⚠️ 未配置 Embedding API (openai_api_base / openai_api_key 为空)"

    import httpx

    url = f"{base_url}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": settings.embedding_model_name, "input": "连通性测试"}

    with httpx.Client(timeout=15.0, trust_env=False) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    vec = data["data"][0]["embedding"]
    dim = len(vec)
    expected_dim = settings.milvus_dim
    dim_match = "✅ 匹配" if dim >= expected_dim else f"⚠️ 不匹配（期望≥{expected_dim}）"
    return f"API 响应正常 | 模型: {settings.embedding_model_name} | 返回维度: {dim} ({dim_match}) | 端点: {base_url}"


def test_backend_api():
    """测试 FastAPI 后端服务是否已启动：调用 /api/health"""
    import httpx

    url = "http://127.0.0.1:8000/api/health"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
        status = data.get("status", "unknown")
        return f"后端运行中 | /api/health 返回: {data} | URL: {url}"
    except httpx.ConnectError:
        return "⚠️ 后端未启动（连接 127.0.0.1:8000 失败），请先运行 python main.py"
    except Exception as e:
        return f"⚠️ 后端异常: {e}"


# ==================== 主流程 ====================

def main():
    """依次执行所有连通性测试，汇总输出结果"""
    print("=" * 60)
    print("🚀 全链路连通性测试")
    print(f"   配置文件: .env (前缀 RAG_)")
    print(f"   环境: {settings.app_env}")
    print("=" * 60)

    # 定义所有测试项：(名称, 测试函数, 超时提示)
    tests = [
        ("MySQL 数据库", test_mysql, f"DSN: ...@{settings.mysql_dsn.split('@')[-1] if '@' in settings.mysql_dsn else settings.mysql_dsn}"),
        ("Redis 缓存", test_redis, f"URL: {settings.redis_url}"),
        ("Milvus 向量数据库", test_milvus, f"URI: {settings.milvus_url}"),
        ("LLM 大语言模型 API", test_llm_api, f"Provider: {settings.llm_provider} | Model: {settings.llm_model_name}"),
        ("Embedding 向量化 API", test_embedding_api, f"Model: {settings.embedding_model_name}"),
        ("FastAPI 后端服务", test_backend_api, "http://127.0.0.1:8000"),
    ]

    results = []
    for name, func, hint in tests:
        passed, detail = test_with_timeout(name, func, hint)
        results.append((name, passed, detail))

    # ==================== 汇总报告 ====================
    print("\n" + "=" * 60)
    print("📊 连通性测试汇总")
    print("=" * 60)

    passed_count = 0
    failed_count = 0
    warn_count = 0

    for name, passed, detail in results:
        if passed:
            if "⚠️" in detail:
                status = "⚠️ 警告"
                warn_count += 1
            else:
                status = "✅ 通过"
                passed_count += 1
        else:
            status = "❌ 失败"
            failed_count += 1
        print(f"  {status}  {name}")

    print("-" * 60)
    total = len(results)
    print(f"  总计: {total} 项 | ✅ 通过: {passed_count} | ⚠️ 警告: {warn_count} | ❌ 失败: {failed_count}")

    if failed_count == 0 and warn_count == 0:
        print("\n🎉 所有服务连接正常，系统可以正常运行！")
    elif failed_count == 0:
        print("\n⚠️ 有部分服务未配置或未启动，核心功能可能受限。")
    else:
        print("\n❌ 有关键服务连接失败，请检查配置和服务状态！")

    print()
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

