# -*- coding: utf-8 -*-
# test_api.py - 测试硅基流动API连接
import os
import time

# 配置（用你真实的密钥）
API_KEY = "sk-eckouuxpspqghmssxbftwhzohxnwfvcpmklfgsuucbtdamui"  # 替换
BASE_URL = "https://api.siliconflow.cn/v1"
MODEL = "Pro/Qwen/Qwen2.5-7B-Instruct"

print("=" * 50)
print("开始测试硅基流动API连接...")
print(f"模型: {MODEL}")
print(f"API地址: {BASE_URL}")
print("=" * 50)

# 测试1: 网络连通性
import requests
print("\n[测试1] 检查网络连通性...")
try:
    response = requests.get("https://api.siliconflow.cn", timeout=5)
    print(f"✅ 网络可达，状态码: {response.status_code}")
except Exception as e:
    print(f"❌ 网络不通: {e}")
    print("   请检查：")
    print("   1. 是否开启代理（需要关闭或正确配置）")
    print("   2. 防火墙是否拦截")
    exit(1)

# 测试2: API调用
print("\n[测试2] 测试API调用...")
from openai import OpenAI

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=30
)

try:
    start = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": "你好，请回复：连接成功"}
        ],
        max_tokens=10,
        temperature=0
    )
    elapsed = time.time() - start
    print(f"✅ API调用成功！耗时: {elapsed:.2f}秒")
    print(f"   回复: {response.choices[0].message.content}")
except Exception as e:
    print(f"❌ API调用失败: {e}")
    print("\n可能原因：")
    print("1. API密钥错误或无效")
    print("2. 账户余额不足")
    print("3. 模型名称不正确")
    print("4. 需要配置代理")

# 测试3: 查看可用模型
print("\n[测试3] 查看账户可用模型...")
try:
    models = client.models.list()
    print("✅ 可用模型列表（前10个）:")
    for i, model in enumerate(models.data[:10]):
        print(f"   - {model.id}")
except Exception as e:
    print(f"⚠️ 无法获取模型列表: {e}")
