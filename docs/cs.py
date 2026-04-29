# -*- coding: utf-8 -*-
"""
RAG 系统评测脚本（适配硅基流动 SiliconFlow API）
核心评测指标：answer_relevancy、context_recall、context_precision
"""
import os
import sys
import warnings

# ========== 解决 Windows 编码问题 ==========
if sys.platform == 'win32':
    import locale

    try:
        locale.setlocale(locale.LC_ALL, 'zh_CN.UTF-8')
    except:
        pass

os.environ['PYTHONIOENCODING'] = 'utf-8'

warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    # answer_relevancy,
    context_recall,
    context_precision
)
from langchain_openai import ChatOpenAI

# ====================== 1. 配置硅基流动 API ======================
# 获取方式：登录 https://siliconflow.cn → API 密钥 → 新建密钥
SILICONFLOW_API_KEY = "sk-eckouuxpspqghmssxbftwhzohxnwfvcpmklfgsuucbtdamui"  # ← 替换为你的真实密钥

# 硅基流动配置（OpenAI 兼容模式）
os.environ["OPENAI_API_KEY"] = SILICONFLOW_API_KEY  # ragas 默认读取此变量

# 硅基流动的 API 地址 [citation:1][citation:5]
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"

# 模型选择（可在 https://siliconflow.cn/models 查看可用模型）[citation:10]
# 推荐免费/高性价比模型：
# - "deepseek-ai/DeepSeek-V3"      # 旗舰模型，效果好
# - "deepseek-ai/DeepSeek-R1"      # 推理能力强
# - "Qwen/Qwen3.5-4B"              # 完全免费
# - "Pro/zai-org/GLM-4.7"          # 智谱旗舰
MODEL_NAME = "Pro/MiniMaxAI/MiniMax-M2.5"  # 可修改为其他模型

# 如果需要代理（根据实际情况取消注释）
# os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
# os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

# ====================== 2. 准备评测数据 ======================
eval_data = {
    "question": [
        "什么是大语言模型？",
        "RAG 技术的核心优势是什么？",
        "如何提升 RAG 的检索精度？"
    ],
    "answer": [
        "大语言模型是基于大规模文本数据训练的深度学习模型，能理解和生成人类语言，例如GPT、LLaMA等。",
        "RAG 可以将检索到的外部知识融入生成过程，解决大模型的知识过期和幻觉问题，同时无需重新训练模型。",
        "提升 RAG 检索精度可以从优化检索算法（如BM25+向量检索）、数据清洗、查询重写、上下文排序等方面入手。"
    ],
    "contexts": [
        ["大语言模型（LLM）是一种基于Transformer架构、通过海量文本训练的人工智能模型，具备强大的自然语言理解和生成能力。"],
        ["检索增强生成（RAG）的核心是将信息检索与生成模型结合，既利用生成模型的语言能力，又通过检索引入最新、最准确的外部知识，避免模型幻觉。"],
        ["优化RAG检索精度的方法：1. 混合检索（关键词+向量）；2. 对查询进行改写和扩展；3. 过滤低质量文档；4. 调整检索Top-K数量。"]
    ],
    "ground_truth": [
        "大语言模型是基于大规模文本数据训练的、具备自然语言理解和生成能力的人工智能模型，典型代表有GPT、LLaMA、文心一言等。",
        "RAG技术的核心优势是解决大语言模型的知识过期问题和生成幻觉问题，同时无需重新训练模型，能快速融入最新知识。",
        "提升RAG检索精度的方法包括：优化检索算法（如混合检索）、查询重写、数据清洗、上下文排序、调整检索参数（如Top-K）等。"
    ]
}

dataset = Dataset.from_dict(eval_data)


# ====================== 3. 执行 RAG 评测 ======================
def evaluate_rag_system():
    try:
        # 配置硅基流动的 LLM（OpenAI 兼容模式）[citation:1]
        llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=SILICONFLOW_API_KEY,
            base_url=SILICONFLOW_BASE_URL,
            temperature=0,
            timeout=60,
            max_retries=3
        )

        metrics = [
            # answer_relevancy,
            context_recall,
            context_precision
        ]

        print("=" * 50)
        print(f"正在使用硅基流动 API 进行评测...")
        print(f"模型：{MODEL_NAME}")
        print("=" * 50)

        result = evaluate(
            dataset=dataset,
            metrics=metrics,
            llm=llm,
        )

        print("\n" + "=" * 50)
        print("RAG 系统评测整体分数：")
        print(result)

        print("\n" + "=" * 50)
        print("每条数据的详细评测结果：")
        result_df = result.to_pandas()
        print(result_df)

        result_df.to_csv("rag_eval_result.csv", index=False, encoding="utf-8-sig")
        print("\n评测结果已保存到 rag_eval_result.csv 文件！")

        return result

    except Exception as e:
        print(f"\n评测过程中出现错误：{str(e)}")
        print("\n错误排查提示：")
        print("1. 检查硅基流动 API 密钥是否正确（格式：sk-开头）")
        print("2. 检查账户是否有余额：https://siliconflow.cn/account/usage")
        print("3. 确认模型名称是否正确（从模型广场复制完整名称）[citation:10]")
        print("4. 检查网络是否能访问 https://api.siliconflow.cn")


if __name__ == "__main__":
    # 检查 API 密钥配置
    if "你的" in SILICONFLOW_API_KEY:
        print("❌ 错误：请先替换真实的硅基流动 API 密钥！")
        print("   获取步骤：")
        print("   1. 访问 https://siliconflow.cn 注册/登录")
        print("   2. 进入「API 密钥」页面，点击「新建 API 密钥」")
        print("   3. 复制密钥（以 sk- 开头）替换到脚本中")
    else:
        evaluate_rag_system()