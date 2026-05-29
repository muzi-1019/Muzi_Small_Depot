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
import logging                       # 日志
from collections.abc import Generator  # 生成器类型注解

from app.core.config import settings          # 全局配置
from app.schemas.character import CharacterOut  # 角色信息数据结构
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)  # 当前模块日志器


class LLMService:
    """大语言模型调用服务：封装了与大模型 API 的所有交互逻辑"""

    @staticmethod
    def generate(character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "") -> str:
        """非流式生成：根据配置的提供商选择调用真实 API 或模拟回复，一次性返回完整回答"""
        provider = (settings.llm_provider or "mock").lower()
        logger.info("LLM generate start: provider=%s, character=%s, question=%s", provider, character.name, question[:60])
        if provider in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:  # 如果是 OpenAI 兼容接口
            answer = LLMService._openai_compatible_chat(character, question, context, memory, realtime_context)  # 调用真实模型接口
            logger.info("LLM generate done: answer_len=%d", len(answer))
            return answer  # 返回真实模型回复
        answer = LLMService._mock(character, question, context, memory, realtime_context)  # 非真实提供商时使用 mock 回复
        logger.info("LLM mock done: answer_len=%d", len(answer))
        return answer

    @staticmethod
    def generate_stream(
        character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "",
    ) -> Generator[str, None, None]:
        """
        流式生成：逐块返回大模型的回复（实现打字机效果）。
        使用 SSE（Server-Sent Events）协议，每接收到一小段文字就立即通过 yield 发送给前端。
        为什么优先使用流式生成：
        - 云端 LLM 首 token 之后会持续输出，流式可以让用户尽早看到结果，而不是等待完整回答；
        - 对长答案尤其明显，能把“系统卡住”的感受变成“正在生成”；
        - 后端只保存最终拼接后的完整答案，流式过程不改变数据库结构。
        为什么使用 OpenAI 兼容协议：
        - SiliconFlow、OpenAI、vLLM、SGLang 都能复用同一套请求结构；
        - 以后切换模型或本地推理服务时，只需要改 base_url/model_name，不需要改业务代码。
        """
        provider = (settings.llm_provider or "mock").lower()
        logger.info("LLM stream start: provider=%s, character=%s, question=%s", provider, character.name, question[:60])
        if provider not in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:  # 非真实模型提供商
            yield LLMService._mock(character, question, context, memory, realtime_context)  # 直接 yield 一整段 mock 回复
            logger.info("LLM mock stream done")
            return  # mock 流结束
        messages = LLMService._build_openai_messages(character, question, context, memory, realtime_context)
        chunk_count = 0  # 统计成功解析并输出的文本块数量
        for line in LLMClient.chat_stream(messages, temperature=0.4, timeout=120.0):  # 按行读取 SSE 数据
            if not line.startswith("data: "):  # OpenAI 流式协议中有效内容以 data: 开头
                continue  # 跳过空行或其他事件
            data_str = line[6:]  # 去掉 "data: " 前缀
            if data_str.strip() == "[DONE]":  # [DONE] 表示模型输出结束
                break  # 退出流读取
            try:  # 单个流式 chunk 可能不是合法 JSON
                chunk = json.loads(data_str)  # 解析 JSON chunk
                delta = chunk["choices"][0]["delta"]  # OpenAI 流式增量字段
                content = delta.get("content", "")  # 获取本次新增文本
                if content:  # 如果当前 chunk 有实际文本
                    chunk_count += 1  # 统计块数
                    yield content  # 将文本块返回给上层 SSE
            except (json.JSONDecodeError, KeyError, IndexError):
                logger.warning("LLM stream parse error: line=%s", line[:120])
                continue  # 跳过异常 chunk，继续读取后续内容
        logger.info("LLM stream done: chunks=%d", chunk_count)

    @staticmethod
    def _mock(character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "") -> str:
        """模拟回复：不调用真实 API，根据角色和检索结果返回模板化的回复（用于开发测试）"""
        style_prefix = f"[{character.name}] "  # mock 回复前缀，用角色名模拟角色身份
        q = (question or "").strip().lower()  # 清洗用户问题，便于判断是否为问候
        chunks = [line.strip() for line in (context or "").split("\n") if line.strip()]  # 将检索上下文按行拆分并去空
        top_context = chunks[:3]  # mock 只展示前 3 条上下文，避免回复太长
        memory_hint = "我们继续上次的话题。" if memory else ""  # 如果有记忆，添加继续上下文提示

        greeting_tokens = {"你好", "您好", "hi", "hello", "哈喽", "在吗"}
        if q in greeting_tokens:  # 如果用户只是打招呼
            if "朋友" in character.name or character.role_type == "social":  # 社交角色使用更亲切的问候
                return f"{style_prefix}你好呀，很高兴见到你。今天过得怎么样？我可以陪你继续聊。"
            return f"{style_prefix}你好，我可以基于知识库里的 PDF 内容帮你回答。请直接提问。"

        if top_context:  # 如果有检索上下文
            bullet_text = "\n".join([f"- {line}" for line in top_context])  # 将上下文整理为项目符号
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
    def _build_openai_messages(
        character: CharacterOut, question: str, context: str, memory: str, realtime_context: str = "",
    ) -> list[dict]:
        """
        构建 OpenAI 兼容格式的 API 请求。
        将角色人设组装为 system 消息，将知识上下文+对话记忆+实时上下文+用户问题组装为 user 消息。
        返回：(请求URL, 请求头, 请求体)
        Prompt 分层设计的取舍：
        - system 放角色人设和硬规则，优先级高，能稳定约束回答风格与引用规则；
        - user 放检索片段、近期记忆和用户问题，便于每轮动态变化；
        - 实时环境数据单独插入，并明确“仅在用户问到时使用”，避免模型主动胡乱提天气/时间。
        """
        system_parts = [  # system prompt 分段构造，便于追加规则
            f"你是「{character.name}」，领域：{character.domain}。",
            f"人设：{character.persona}",
        ]
        if character.prompt_template:  # 如果角色配置了提示模板
            system_parts.append(f"提示模板：{character.prompt_template}")  # 加入 system prompt
        system_parts.append(
            "\n【核心规则】系统会在后台提供真实环境数据（时间、天气等）。"
            "仅当用户主动询问时间、天气、温度、位置等问题时，才引用这些数据回答，数值必须与系统数据完全一致。"
            "用户没有问到这些信息时，不要主动提及时间和天气，正常对话即可。"
        )
        system_parts.append(
            "\n【引用规则】当你引用检索到的知识片段回答用户问题时，请在相关陈述后标注来源编号，如\"根据[1]所述…\"或\"参考[2]…\"。"
            "如果回答中没有直接引用检索到的知识，则不需要标注来源编号。"
        )
        system = "\n".join(system_parts)  # 合并 system prompt
        user_block = (  # 构造用户消息块，包含 RAG 上下文、记忆和当前问题
            f"【检索到的知识片段】\n{context or '（无）'}\n\n"
            f"【近期对话记忆】\n{memory or '（无）'}\n\n"
            f"【用户问题】\n{question}"
        )
        messages = [{"role": "system", "content": system}]  # 初始化消息列表，先放 system
        if realtime_context:  # 如果有实时环境上下文
            messages.append({"role": "user", "content": f"【系统环境数据（备用）】以下是真实数据，仅当用户问到时间或天气时才使用，平时不要主动提及：\n{realtime_context}"})
            messages.append({"role": "assistant", "content": f"明白，我已记住这些环境数据，只在用户主动问到时间、天气等问题时才引用，平时正常聊天不会主动提及。"})
        messages.append({"role": "user", "content": user_block})  # 最后追加本轮用户问题和检索上下文
        return messages  # 返回 OpenAI 兼容消息列表

    @staticmethod
    def summarize(conversation_text: str) -> str:
        """调用大模型对历史对话进行摘要总结（用于压缩过长的对话记忆，节省 Token）"""
        provider = (settings.llm_provider or "mock").lower()  # 读取模型提供商
        if provider not in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:  # mock/非真实模型模式
            lines = conversation_text.strip().split("\n")  # 简单按行统计对话条数
            return f"（前文共 {len(lines)} 条对话，主要围绕用户提出的问题展开。）"  # 返回模板摘要

        messages = [
            {"role": "system", "content": "你是一个对话摘要助手。请将以下对话历史压缩为一段简洁的中文摘要（不超过200字），保留关键信息和上下文。"},
            {"role": "user", "content": conversation_text},
        ]
        try:  # 摘要失败不应影响主对话
            data = LLMClient.chat(messages, temperature=0.3, max_tokens=300, timeout=30.0)
            return LLMClient.extract_message_content(data)  # 返回摘要文本
        except Exception:  # 捕获所有外部调用异常
            return ""  # 返回空摘要作为降级

    @staticmethod
    def rewrite_query(question: str, memory: str) -> str:
        """多轮对话指代消解：结合对话历史，将用户的模糊/简短问题改写为独立完整的检索查询。
        例如：用户问"他的营收呢？" → 结合对话历史，改写为"武汉兴图新科电子股份有限公司的营业收入是多少？"
        """
        if not memory or not memory.strip():  # 没有历史记忆时无法做指代消解
            return question  # 直接返回原问题
        provider = (settings.llm_provider or "mock").lower()  # 读取模型提供商
        if provider not in {"openai", "vllm", "sglang", "siliconflow", "silicon_flow", "silicon-flow"}:  # 非真实模型模式
            return question  # 不做改写
        messages = [
            {"role": "system", "content": (
                "你是一个 Query Rewriting 助手。根据对话历史，将用户最新的问题改写为一个独立、完整、适合检索的查询。\n"
                "规则：\n"
                "1. 解析指代词（他/她/它/这个公司/该产品等）替换为具体实体名称\n"
                "2. 补全省略的主语或上下文\n"
                "3. 如果问题已经足够清晰，直接返回原问题\n"
                "4. 只返回改写后的问题，不要解释"
            )},
            {"role": "user", "content": f"对话历史：\n{memory[-1500:]}\n\n用户最新问题：{question}\n\n改写后的检索查询："},
        ]
        try:  # 改写失败时直接回退原问题
            data = LLMClient.chat(messages, temperature=0.1, max_tokens=150, timeout=10.0)
            rewritten = LLMClient.extract_message_content(data)  # 提取改写结果
            if rewritten and len(rewritten) < 500:  # 只接受非空且长度合理的改写
                return rewritten  # 返回改写后的检索查询
        except Exception:  # 捕获改写过程异常
            pass  # 静默降级
        return question  # 回退原问题

    @staticmethod
    def _openai_compatible_chat(
        character: CharacterOut,
        question: str,
        context: str,
        memory: str,
        realtime_context: str = "",
    ) -> str:
        """调用 OpenAI 兼容 API 获取非流式完整回复"""
        messages = LLMService._build_openai_messages(character, question, context, memory, realtime_context)  # 构造请求消息
        data = LLMClient.chat(messages, temperature=0.4, timeout=120.0)  # 发送非流式请求
        return LLMClient.extract_message_content(data)  # 提取模型回复文本
