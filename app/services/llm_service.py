"""
本文件的作用：大语言模型（LLM）调用服务。
负责将用户问题、检索到的知识上下文、角色人设组装成提示词，然后调用大模型 API 获取回答。

支持两种模式：
1. mock 模式：不调用真实 API，返回模板化的模拟回复（用于开发测试）
2. openai/siliconflow 等模式：调用兼容 OpenAI 格式的 API（如硅基流动 SiliconFlow）

支持两种回复方式：
- generate：一次性返回完整回复（非流式）
- generate_stream：逐字逐句返回（流式，打字机效果）
"""

import json                          # JSON 解析工具
from collections.abc import Generator  # 生成器类型注解

import httpx  # HTTP 客户端库，用于调用外部 API

from app.core.config import settings          # 全局配置
from app.schemas.character import CharacterOut  # 角色信息数据结构


class LLMService:
    """大语言模型调用服务：封装了与大模型 API 的所有交互逻辑"""

    @staticmethod
    def generate(character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "") -> str:
        """非流式生成：根据配置的提供商选择调用真实 API 或模拟回复，一次性返回完整回答"""
        provider = (settings.llm_provider or "mock").lower()  # 获取大模型提供商配置
        if provider in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:
            return LLMService._openai_compatible_chat(character, question, context, memory, realtime_context)  # 调用真实 API
        return LLMService._mock(character, question, context, memory, realtime_context)  # 使用模拟回复

    @staticmethod
    def generate_stream(
        character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "",
    ) -> Generator[str, None, None]:
        """
        流式生成：逐块返回大模型的回复（实现打字机效果）。
        使用 SSE（Server-Sent Events）协议，每接收到一小段文字就立即通过 yield 发送给前端。
        """
        provider = (settings.llm_provider or "mock").lower()
        if provider not in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:
            yield LLMService._mock(character, question, context, memory, realtime_context)  # 非真实 API 时直接返回模拟回复
            return
        url, headers, payload = LLMService._build_openai_request(character, question, context, memory, realtime_context)  # 构建请求
        payload["stream"] = True  # 开启流式模式
        with httpx.Client(timeout=120.0, trust_env=False) as client:  # 创建 HTTP 客户端（超时120秒）
            with client.stream("POST", url, headers=headers, json=payload) as resp:  # 发送流式 POST 请求
                resp.raise_for_status()  # 检查 HTTP 状态码
                for line in resp.iter_lines():  # 逐行读取 SSE 响应
                    if not line.startswith("data: "):  # 跳过非数据行
                        continue
                    data_str = line[6:]  # 去掉 "data: " 前缀
                    if data_str.strip() == "[DONE]":  # 流结束标志
                        break
                    try:
                        chunk = json.loads(data_str)  # 解析 JSON 数据
                        delta = chunk["choices"][0]["delta"]  # 获取增量内容
                        content = delta.get("content", "")  # 提取文本片段
                        if content:
                            yield content  # 将文本片段发送给调用者
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue  # 解析失败则跳过

    @staticmethod
    def _mock(character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "") -> str:
        """模拟回复：不调用真实 API，根据角色和检索结果返回模板化的回复（用于开发测试）"""
        style_prefix = f"[{character.name}] "
        q = (question or "").strip().lower()
        chunks = [line.strip() for line in (context or "").split("\n") if line.strip()]
        top_context = chunks[:3]
        memory_hint = "我们继续上次的话题。" if memory else ""

        greeting_tokens = {"你好", "您好", "hi", "hello", "哈喽", "在吗"}
        if q in greeting_tokens:
            if "朋友" in character.name or character.role_type == "social":
                return f"{style_prefix}你好呀，很高兴见到你。今天过得怎么样？我可以陪你继续聊。"
            return f"{style_prefix}你好，我可以基于知识库里的 PDF 内容帮你回答。请直接提问。"

        if top_context:
            bullet_text = "\n".join([f"- {line}" for line in top_context])
            return (
                f"{style_prefix}{memory_hint}"
                f"我根据检索到的 PDF 知识整理如下：\n{bullet_text}\n\n"
                f"针对你的问题“{question}”，建议结合上面的知识点进一步判断。"
            ).strip()

        return (
            f"{style_prefix}{memory_hint}"
            f"当前没有检索到足够相关的 PDF 内容。"
            f"你可以尝试换一个更具体的问题，或者确认向量库中已完成 PDF 入库。"
        )

    @staticmethod
    def _build_openai_request(
        character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "",
    ) -> tuple[str, dict, dict]:
        """
        构建 OpenAI 兼容格式的 API 请求。
        将角色人设组装为 system 消息，将知识上下文+对话记忆+实时上下文+用户问题组装为 user 消息。
        返回：(请求URL, 请求头, 请求体)
        """
        system_parts = [
            f"你是「{character.name}」，领域：{character.domain}。",
            f"人设：{character.persona}",
        ]
        if character.prompt_template:
            system_parts.append(f"提示模板：{character.prompt_template}")
        system_parts.append(
            "\n【核心规则】系统会在后台提供真实环境数据（时间、天气等）。"
            "仅当用户主动询问时间、天气、温度、位置等问题时，才引用这些数据回答，数值必须与系统数据完全一致。"
            "用户没有问到这些信息时，不要主动提及时间和天气，正常对话即可。"
        )
        system_parts.append(
            "\n【引用规则】当你引用检索到的知识片段回答用户问题时，请在相关陈述后标注来源编号，如\"根据[1]所述…\"或\"参考[2]…\"。"
            "如果回答中没有直接引用检索到的知识，则不需要标注来源编号。"
        )
        system = "\n".join(system_parts)
        user_block = (
            f"【检索到的知识片段】\n{context or '（无）'}\n\n"
            f"【近期对话记忆】\n{memory or '（无）'}\n\n"
            f"【用户问题】\n{question}"
        )
        base_url = (settings.openai_api_base or "").rstrip("/")
        if not base_url:
            raise RuntimeError("未配置大模型接口地址，请设置 RAG_OPENAI_API_BASE")
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        messages = [{"role": "system", "content": system}]
        if realtime_context:
            messages.append({"role": "user", "content": f"【系统环境数据（备用）】以下是真实数据，仅当用户问到时间或天气时才使用，平时不要主动提及：\n{realtime_context}"})
            messages.append({"role": "assistant", "content": f"明白，我已记住这些环境数据，只在用户主动问到时间、天气等问题时才引用，平时正常聊天不会主动提及。"})
        messages.append({"role": "user", "content": user_block})
        payload = {
            "model": settings.llm_model_name,
            "messages": messages,
            "temperature": 0.4,
        }
        return url, headers, payload

    @staticmethod
    def summarize(conversation_text: str) -> str:
        """调用大模型对历史对话进行摘要总结（用于压缩过长的对话记忆，节省 Token）"""
        provider = (settings.llm_provider or "mock").lower()
        if provider not in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:
            lines = conversation_text.strip().split("\n")
            return f"（前文共 {len(lines)} 条对话，主要围绕用户提出的问题展开。）"

        base_url = (settings.openai_api_base or "").rstrip("/")
        if not base_url:
            return ""
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {
            "model": settings.llm_model_name,
            "messages": [
                {"role": "system", "content": "你是一个对话摘要助手。请将以下对话历史压缩为一段简洁的中文摘要（不超过200字），保留关键信息和上下文。"},
                {"role": "user", "content": conversation_text},
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""

    @staticmethod
    def _openai_compatible_chat(
        character: CharacterOut,
        question: str,
        context: str,
        memory: str,
        realtime_context: str = "",
    ) -> str:
        """调用 OpenAI 兼容 API 获取非流式完整回复"""
        url, headers, payload = LLMService._build_openai_request(character, question, context, memory, realtime_context)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response: {data}") from exc
