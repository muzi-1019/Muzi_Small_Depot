"""
全链路 API 功能测试脚本。
依次测试：注册→登录→角色列表→发送聊天→流式聊天→会话列表→知识库→图谱→管理后台。
自动输出每个接口的状态和返回概要，方便快速定位 bug。

用法：python test_api_flow.py
"""

import sys
import os
import json
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx

BASE = "http://127.0.0.1:8000"
# 测试用户（密码至少 6 位）
TEST_USER = "testbug_" + str(int(time.time()) % 100000)
TEST_PASS = "test123456"

results = []  # (name, passed, detail)


def test(name, method, path, **kwargs):
    """执行单个 API 测试"""
    url = BASE + path
    expect_status = kwargs.pop("expect_status", 200)
    print(f"\n{'='*60}")
    print(f"🔍 {name}")
    print(f"   {method.upper()} {path}")
    try:
        r = getattr(httpx, method)(url, timeout=60, **kwargs)
        status_ok = r.status_code == expect_status
        body = r.text[:300]
        try:
            data = r.json()
        except Exception:
            data = None

        if status_ok:
            print(f"   ✅ {r.status_code}  {body[:150]}")
        else:
            print(f"   ❌ 期望 {expect_status}, 实际 {r.status_code}")
            print(f"   Body: {body}")

        results.append((name, status_ok, r.status_code, body[:100]))
        return data, r.status_code
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        results.append((name, False, 0, str(e)[:100]))
        return None, 0


def main():
    token = ""
    headers = {}
    char_id = None

    # ========== 1. 注册 ==========
    data, code = test("注册新用户", "post", "/api/v1/auth/register",
                       json={"account": TEST_USER, "password": TEST_PASS})

    # ========== 2. 登录 ==========
    data, code = test("用户登录", "post", "/api/v1/auth/login",
                       json={"account": TEST_USER, "password": TEST_PASS})
    user_id = None
    if data and "access_token" in data:
        token = data["access_token"]
        user_id = data.get("user_id")
        headers = {"Authorization": f"Bearer {token}"}
        print(f"   Token: {token[:30]}...  user_id: {user_id}")
    else:
        print("   ⚠️ 未获取到 token，后续测试可能失败")

    # ========== 3. 当前用户信息 ==========
    test("获取当前用户", "get", "/api/v1/auth/me", headers=headers)

    # ========== 4. 角色列表 ==========
    data, code = test("角色列表", "get", "/api/v1/characters", headers=headers)
    # 优先使用 character_id=6（招股说明书角色，有知识库数据）
    if data and isinstance(data, list):
        c6 = [c for c in data if c.get("id") == 6]
        if c6:
            char_id = 6
            print(f"   使用角色: id=6, name={c6[0].get('name','')[:20]}（有知识库数据）")
        else:
            char_id = data[0].get("id", 1)
            print(f"   使用角色: id={char_id}, name={data[0].get('name', '')[:20]}")
    else:
        char_id = 6
        print(f"   ⚠️ 无角色返回，使用默认 char_id={char_id}")

    # ========== 5. 发送聊天（非流式）==========
    chat_payload = {"user_id": user_id, "character_id": char_id, "question": "你好，请简单介绍一下自己"}
    data, code = test("发送聊天（非流式）", "post", "/api/v1/chat/send",
                       headers=headers, json=chat_payload)
    if data and isinstance(data, dict):
        chat_data = data.get("data", {})
        reply = chat_data.get("answer", "")
        print(f"   回复: {str(reply)[:100]}...")

    # ========== 6. 流式聊天 ==========
    print(f"\n{'='*60}")
    print(f"🔍 流式聊天测试")
    print(f"   POST /api/v1/chat/stream")
    conversation_id = None
    try:
        stream_payload = {"user_id": user_id, "character_id": char_id, "question": "公司的主营业务是什么？"}
        with httpx.stream("POST", BASE + "/api/v1/chat/stream",
                          headers=headers, json=stream_payload, timeout=60) as resp:
            if resp.status_code != 200:
                resp.read()
                print(f"   ❌ Status: {resp.status_code}  Body: {resp.text[:200]}")
                results.append(("流式聊天", False, resp.status_code, resp.text[:80]))
            else:
                chunks = []
                raw_lines = []
                for line in resp.iter_lines():
                    raw_lines.append(line)
                    if line.startswith("data: "):
                        chunk_data = line[6:]
                        if chunk_data == "[DONE]":
                            break
                        try:
                            obj = json.loads(chunk_data)
                            token = obj.get("chunk") or obj.get("token") or obj.get("content") or obj.get("delta", "")
                            if token:
                                chunks.append(token)
                        except json.JSONDecodeError:
                            chunks.append(chunk_data)
                full_reply = "".join(chunks)
                if full_reply:
                    print(f"   ✅ 流式回复 ({len(chunks)} chunks): {full_reply[:120]}...")
                    results.append(("流式聊天", True, 200, full_reply[:80]))
                else:
                    print(f"   ⚠️ 状态200但无可解析的流式 token，收到 {len(raw_lines)} 行原始数据:")
                    for rl in raw_lines[:5]:
                        print(f"      {rl[:120]}")
                    results.append(("流式聊天", False, 200, f"无token, {len(raw_lines)}行原始数据"))
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        results.append(("流式聊天", False, 0, str(e)[:100]))

    # ========== 7. 会话列表 ==========
    data, code = test("会话列表", "get", f"/api/v1/chat/conversations?user_id={user_id}", headers=headers)
    conversation_id = None
    if data and isinstance(data, dict):
        convs = data.get("data", [])
        if convs:
            conversation_id = convs[0].get("id")
            print(f"   最新会话: id={conversation_id}, title={convs[0].get('title','')[:30]}")

    # ========== 8. 聊天历史 ==========
    if conversation_id:
        test("聊天历史", "get", f"/api/v1/chat/history?user_id={user_id}&conversation_id={conversation_id}", headers=headers)

    # ========== 9. 知识库文档列表 ==========
    test("知识库文档列表", "get", f"/api/v1/knowledge/list?character_id={char_id}", headers=headers)

    # ========== 10. 图谱统计 ==========
    test("知识图谱统计", "get", f"/api/v1/graph/stats?character_id={char_id}", headers=headers)

    # ========== 11. 图谱查询 ==========
    test("知识图谱查询", "get", f"/api/v1/graph/search?character_id={char_id}&query=控股股东",
         headers=headers)

    # ========== 12. 管理后台统计（非管理员应返回 403）==========
    test("管理后台统计（非管理员→403）", "get", "/api/v1/admin/stats",
         headers=headers, expect_status=403)

    # ========== 汇总 ==========
    print(f"\n{'='*60}")
    print(f"📊 测试汇总")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    for name, ok, status, detail in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} [{status}] {name}")
        if not ok:
            print(f"       → {detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  总计: {len(results)} | ✅ 通过: {passed} | ❌ 失败: {failed}")
    if failed == 0:
        print("\n🎉 所有 API 测试通过！")
    else:
        print(f"\n⚠️ 有 {failed} 项失败，需要排查修复。")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
