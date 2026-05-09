"""
本文件的作用：聊天业务服务（系统最核心的业务逻辑文件）。
完整处理用户发送消息的全流程：
1. 验证用户和角色是否存在
2. 检查角色并发槽位
3. 管理对话会话（创建/查询）
4. 检查屏蔽词
5. 获取短期对话记忆（让 AI 记住上下文）
6. 从向量知识库检索相关知识片段（RAG）
7. 调用大模型生成回复（支持流式/非流式）
8. 保存对话消息到数据库
9. 自动总结过长的对话记忆

同时提供对话管理功能：查看历史、列出会话、删除会话、重命名、导出等。
"""

import json                          # JSON 序列化工具
import logging                       # 日志
import re                            # 正则表达式，用于屏蔽词匹配
import time                          # 时间控制，用于等待动画
from collections.abc import Generator  # 生成器类型注解
from concurrent.futures import ThreadPoolExecutor  # 线程池，用于并行检索

from fastapi import HTTPException  # HTTP 异常类

logger = logging.getLogger(__name__)

from app.core.blocked_words import BLOCKED_WORDS                        # 屏蔽词列表
from app.core.config import settings                                    # 全局配置
from app.repositories.character_repository import CharacterRepository    # 角色数据访问层
from app.repositories.conversation_repository import ConversationRepository  # 会话数据访问层
from app.repositories.user_repository import UserRepository              # 用户数据访问层
from app.schemas.chat import ChatData, ChatRequest, ChatResponse, ConversationItem, ConversationListResponse, ConversationResponse, HistoryItem, HistoryResponse  # 数据结构定义
from app.services.context_service import ContextService                    # 实时上下文服务（时间/地点/天气）
from app.services.image_understanding_service import ImageUnderstandingService  # 图片理解服务（OCR + 视觉描述）
from app.services.llm_service import LLMService                          # 大模型调用服务
from app.services.memory_service import MemoryService                    # 对话记忆服务
from app.services.graph_service import KnowledgeGraphService            # 知识图谱服务
from app.services.pdf_ingest_service import PDFIngestService             # PDF 向量检索服务


class ChatService:
    """聊天业务服务：系统的核心业务逻辑，编排整个对话流程"""

    def __init__(
        self,
        character_repository: CharacterRepository,   # 角色数据访问层
        user_repository: UserRepository,             # 用户数据访问层
        conversation_repository: ConversationRepository,  # 会话数据访问层
        memory_service: MemoryService,               # 对话记忆服务
    ) -> None:
        self.character_repository = character_repository
        self.user_repository = user_repository
        self.conversation_repository = conversation_repository
        self.memory_service = memory_service
        self.llm_service = LLMService()              # 大模型调用服务
        self.pdf_ingest_service = PDFIngestService()  # PDF 向量检索服务
        self.graph_service = KnowledgeGraphService()  # 知识图谱服务

    def send_message(self, payload: ChatRequest, client_ip: str | None = None) -> ChatResponse:
        """
        非流式发送消息：接收用户问题，返回完整的 AI 回复。
        完整流程：验证用户/角色 → 检查槽位 → 管理会话 → 检查屏蔽词 → 获取记忆 → RAG检索 → 获取实时上下文 → 调用大模型 → 保存消息
        """
        logger.info("chat request start stream=%s user_id=%s character_id=%s conversation_id=%s has_image=%s question_len=%d", False, payload.user_id, payload.character_id, payload.conversation_id, bool(payload.image_data), len(payload.question or ""))
        logger.info("chat request start stream=%s user_id=%s character_id=%s conversation_id=%s has_image=%s question_len=%d", True, payload.user_id, payload.character_id, payload.conversation_id, bool(payload.image_data), len(payload.question or ""))
        user = self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        character = self.character_repository.get_by_id(payload.character_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        self.memory_service.ensure_concurrent_role_slot(payload.user_id, payload.character_id)

        display_question = self._display_question(payload)
        image_context = ImageUnderstandingService.analyze(payload.image_data, payload.image_mime)
        logger.info("chat image context ready user_id=%s character_id=%s context_len=%d", payload.user_id, payload.character_id, len(image_context))
        conv_id = payload.conversation_id
        if conv_id:
            conv = self.conversation_repository.get_by_id(conv_id)
            if not conv or conv.user_id != payload.user_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conv = self.conversation_repository.create_conversation(
                payload.user_id, payload.character_id, title=display_question[:18] or "新对话"
            )
            conv_id = conv.id

        rag_used = False
        sources: list[dict[str, object]] = []
        if self._contains_blocked_word(payload.question):
            answer = "抱歉，我无法回答这个问题。"
        else:
            try:
                memory = self.memory_service.get_recent_context(payload.user_id, payload.character_id, conv_id)
            except Exception as exc:
                logger.error("get memory failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
                memory = ""
            retrieval_query = self.llm_service.rewrite_query(payload.question, memory) if settings.query_rewrite_enabled else payload.question
            if getattr(payload, 'force_no_rag', False):
                context, sources = "", []
            else:
                context, sources = self._retrieve_context(payload.character_id, retrieval_query)
            rag_used = bool(context.strip())
            realtime_ctx = ContextService.get_realtime_context(client_ip, payload.latitude, payload.longitude)
            llm_question = self._question_with_image_context(payload.question, image_context)
            answer = self.llm_service.generate(
                character=character,
                question=llm_question,
                context=context,
                memory=memory,
                realtime_context=realtime_ctx,
            )

        try:
            self.conversation_repository.add_message(conv_id, display_question, answer, rag_used=rag_used, sources=sources)
            self.conversation_repository.update_conversation(conv_id, title=conv.title or display_question[:18], preview=display_question[:120])
        except Exception as exc:
            logger.error("save conversation failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
            raise

        try:
            self.memory_service.append_round(
                user_id=payload.user_id,
                character_id=payload.character_id,
                human=display_question,
                ai=answer,
                conversation_id=conv_id,
            )
        except Exception as exc:
            logger.error("append memory failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)

        self._maybe_summarize(payload.user_id, payload.character_id, conv_id)

        answer = self._filter_blocked_words(answer)

        return ChatResponse(data=ChatData(answer=answer, retrieve_knowledge=sources, rag_used=rag_used))

    def send_message_stream(self, payload: ChatRequest, client_ip: str | None = None) -> Generator[str, None, None]:
        """
        流式发送消息：接收用户问题，通过 SSE 逐字返回 AI 回复（打字机效果）。
        与 send_message 逻辑相同，但回复是分块通过 yield 返回的。
        流中会依次发送：conversation_id → rag_used标志 → 文本块 → [DONE]
        为什么选择 SSE 而不是 WebSocket：
        - 当前业务主要是“服务端持续推送模型生成结果”，属于单向流式输出，SSE 更轻量；
        - SSE 基于普通 HTTP，和 FastAPI StreamingResponse、浏览器 EventSource/Fetch 流更容易集成；
        - 相比 WebSocket，不需要额外维护连接状态和双向协议，适合聊天答案流式展示。
        为什么保留非流式 send_message：
        - 前端交互使用流式接口提升体验；
        - 压测、后台任务、自动评估更适合一次性拿完整 JSON，便于统计耗时和成功率。
        """
        user = self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        character = self.character_repository.get_by_id(payload.character_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        self.memory_service.ensure_concurrent_role_slot(payload.user_id, payload.character_id)

        display_question = self._display_question(payload)
        image_context = ImageUnderstandingService.analyze(payload.image_data, payload.image_mime)
        logger.info("chat image context ready user_id=%s character_id=%s context_len=%d", payload.user_id, payload.character_id, len(image_context))
        conv_id = payload.conversation_id
        if conv_id:
            conv = self.conversation_repository.get_by_id(conv_id)
            if not conv or conv.user_id != payload.user_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conv = self.conversation_repository.create_conversation(
                payload.user_id, payload.character_id, title=display_question[:18] or "新对话"
            )
            conv_id = conv.id

        yield f"data: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

        if self._contains_blocked_word(payload.question):
            refusal = "抱歉，我无法回答这个问题。"
            yield f"data: {json.dumps({'chunk': refusal}, ensure_ascii=False)}\n\n"
            try:
                self.conversation_repository.add_message(conv_id, display_question, refusal, sources=[])
                self.conversation_repository.update_conversation(conv_id, title=conv.title or display_question[:18], preview=display_question[:120])
            except Exception as exc:
                logger.error("save refusal failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
            try:
                self.memory_service.append_round(user_id=payload.user_id, character_id=payload.character_id, human=display_question, ai=refusal, conversation_id=conv_id)
            except Exception as exc:
                logger.error("append refusal memory failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
            yield "data: [DONE]\n\n"
            return

        try:
            memory = self.memory_service.get_recent_context(payload.user_id, payload.character_id, conv_id)
        except Exception as exc:
            logger.error("get memory failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
            memory = ""
        retrieval_query = self.llm_service.rewrite_query(payload.question, memory) if settings.query_rewrite_enabled else payload.question
        logger.info("rewrite_query done: %s", retrieval_query[:80])
        if getattr(payload, 'force_no_rag', False):
            context, sources = "", []
        else:
            # 并行执行检索 + 等待动画：检索可能涉及 Embedding、Milvus、Rerank 等多个耗时步骤。
            # 这里不让前端“空白等待”，而是在后台检索的同时输出提示文字；检索完成则立刻停止提示。
            # 相比同步阻塞等待，这种方式能降低用户感知延迟，也能证明后端仍在正常工作。
            with ThreadPoolExecutor(max_workers=1) as executor:
                retrieve_future = executor.submit(self._retrieve_context, payload.character_id, retrieval_query)
                wait_text = "正在进行检索，请稍等……"
                for ch in wait_text:
                    if retrieve_future.done():
                        break
                    yield f"data: {json.dumps({'chunk': ch}, ensure_ascii=False)}\n\n"
                    time.sleep(0.2)
                try:
                    context, sources = retrieve_future.result()
                except Exception as e:
                    logger.error("retrieve error: %s", e, exc_info=True)
                    context, sources = "", []
        rag_used = bool(context.strip())
        logger.info("RAG done: rag_used=%s, sources=%d, context_len=%d", rag_used, len(sources), len(context))
        # 清空之前的等待提示文字，开始正式回复。
        # 前端收到 replace="" 后会把“正在检索”的占位内容移除，避免提示语和正式答案混在一起。
        yield f"data: {json.dumps({'replace': ''}, ensure_ascii=False)}\n\n"
        realtime_ctx = ContextService.get_realtime_context(client_ip, payload.latitude, payload.longitude)
        yield f"data: {json.dumps({'rag_used': rag_used}, ensure_ascii=False)}\n\n"
        full_answer_parts: list[str] = []
        blocked = False
        chunk_count = 0

        try:
            llm_question = self._question_with_image_context(payload.question, image_context)
            for chunk in self.llm_service.generate_stream(character=character, question=llm_question, context=context, memory=memory, realtime_context=realtime_ctx):
                chunk_count += 1
                full_answer_parts.append(chunk)
                current_text = "".join(full_answer_parts)
                if not blocked and self._contains_blocked_word(current_text):
                    blocked = True
                if not blocked:
                    yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            import traceback
            logger.error("LLM stream error after %d chunks: %s", chunk_count, e, exc_info=True)
            traceback.print_exc()
            yield f"data: {json.dumps({'error': f'生成中断: {type(e).__name__}'}, ensure_ascii=False)}\n\n"

        full_answer = "".join(full_answer_parts)
        logger.info("LLM done: %d chunks, answer_len=%d", chunk_count, len(full_answer))

        if blocked:
            refusal = "抱歉，我无法回答这个问题。"
            yield f"data: {json.dumps({'replace': refusal}, ensure_ascii=False)}\n\n"
            full_answer = refusal
            sources = []

        try:
            self.conversation_repository.add_message(conv_id, display_question, full_answer, rag_used=rag_used, sources=sources)
            self.conversation_repository.update_conversation(conv_id, title=conv.title or display_question[:18], preview=display_question[:120])
        except Exception as exc:
            logger.error("save conversation failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)
            yield f"data: {json.dumps({'error': '消息保存失败，请查看后端日志'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            self.memory_service.append_round(user_id=payload.user_id, character_id=payload.character_id, human=display_question, ai=full_answer, conversation_id=conv_id)
        except Exception as exc:
            logger.error("append memory failed user_id=%s character_id=%s conversation_id=%s: %s", payload.user_id, payload.character_id, conv_id, exc, exc_info=True)

        self._maybe_summarize(payload.user_id, payload.character_id, conv_id)

        if sources:
            yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    @staticmethod
    def _display_question(payload: ChatRequest) -> str:
        """生成用于前端历史记录和会话预览的用户消息文本"""
        question = (payload.question or "").strip()
        if payload.image_data:
            return f"{question} [已上传图片]" if question else "[已上传图片]"
        return question

    @staticmethod
    def _question_with_image_context(question: str, image_context: str) -> str:
        """把图片 OCR/视觉描述拼入用户问题，让 LLM 同时理解文字问题和图片内容"""
        if not image_context:
            return question
        return (
            f"{question}\n\n"
            f"【用户上传图片解析结果】\n{image_context}\n\n"
            "请结合用户问题和图片解析结果回答；如果图片中有文字，请优先依据 OCR 文字；如果没有文字，请依据视觉描述进行分析。"
        )

    def export_conversation(self, user_id: int, conversation_id: int) -> str:
        """导出对话为 Markdown 格式的文本（可下载保存）"""
        conv = self.conversation_repository.get_by_id(conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        rows = self.conversation_repository.list_messages(conversation_id, limit=9999)
        title = conv.title or "对话记录"
        lines = [f"# {title}\n"]
        for m in rows:
            t = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
            lines.append(f"**用户** ({t})\n\n{m.user_message}\n")
            lines.append(f"**AI** ({t})\n\n{self._filter_blocked_words(m.ai_reply)}\n")
            lines.append("---\n")
        return "\n".join(lines)

    def history(self, user_id: int, conversation_id: int, limit: int = 50) -> HistoryResponse:
        """获取指定会话的历史消息列表（包含每条消息的 rag_used 标志）"""
        if not self.user_repository.get_by_id(user_id):
            raise HTTPException(status_code=404, detail="User not found")

        conv = self.conversation_repository.get_by_id(conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")

        rows = self.conversation_repository.list_messages(conversation_id, limit=limit)
        data = []
        for m in rows:
            sources = []
            raw = getattr(m, 'sources_json', '') or ''
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        sources = parsed
                except Exception:
                    pass
            data.append(
                HistoryItem(
                    user_message=m.user_message,
                    ai_reply=self._filter_blocked_words(m.ai_reply),
                    rag_used=getattr(m, 'rag_used', False),
                    sources=sources,
                    created_at=m.created_at,
                )
            )
        return HistoryResponse(data=data)

    def list_conversations(self, user_id: int, character_id: int | None = None) -> ConversationListResponse:
        """获取用户的会话列表（可按角色ID过滤）"""
        if not self.user_repository.get_by_id(user_id):
            raise HTTPException(status_code=404, detail="User not found")
        rows = self.conversation_repository.list_conversations(user_id, character_id)
        data = [
            ConversationItem(
                id=row.id,
                user_id=row.user_id,
                character_id=row.character_id,
                title=row.title or "",
                preview=row.preview or "",
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return ConversationListResponse(data=data)

    def delete_conversation(self, user_id: int, conversation_id: int) -> dict:
        """删除指定会话（会自动归档到备份表）"""
        conv = self.conversation_repository.get_by_id(conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        self.conversation_repository.delete_conversation(conversation_id)
        return {"code": 200, "message": "deleted"}

    def rename_conversation(self, user_id: int, conversation_id: int, title: str) -> ConversationResponse:
        """重命名指定会话"""
        conv = self.conversation_repository.get_by_id(conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        updated = self.conversation_repository.update_conversation(conversation_id, title=title)
        item = ConversationItem(
            id=updated.id,
            user_id=updated.user_id,
            character_id=updated.character_id,
            title=updated.title or "",
            preview=updated.preview or "",
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )
        return ConversationResponse(data=item)

    def create_conversation(self, user_id: int, character_id: int, title: str) -> ConversationResponse:
        """手动创建一个新的空会话"""
        if not self.user_repository.get_by_id(user_id):
            raise HTTPException(status_code=404, detail="User not found")
        if not self.character_repository.get_by_id(character_id):
            raise HTTPException(status_code=404, detail="Character not found")
        conv = self.conversation_repository.create_conversation(user_id, character_id, title=title or "新对话")
        item = ConversationItem(
            id=conv.id,
            user_id=conv.user_id,
            character_id=conv.character_id,
            title=conv.title,
            preview=conv.preview,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
        return ConversationResponse(data=item)

    def _maybe_summarize(self, user_id: int, character_id: int, conversation_id: int) -> None:
        """检查是否需要自动总结对话记忆（当对话轮数达到阈值的整数倍时触发）"""
        rounds = self.memory_service.get_round_count(user_id, character_id, conversation_id)
        threshold = settings.auto_summary_threshold
        if rounds < threshold:
            return
        if rounds % threshold != 0:
            return
        memory_text = self.memory_service.get_recent_context(user_id, character_id, conversation_id)
        if not memory_text.strip():
            return
        try:
            summary = self.llm_service.summarize(memory_text)
            if summary:
                self.memory_service.set_summary(user_id, character_id, summary, conversation_id)
        except Exception:
            pass

    def _retrieve_context(self, character_id: int, question: str) -> tuple[str, list[dict[str, object]]]:
        """
        RAG 检索：从 Milvus 向量库中检索与用户问题相关的知识片段。
        返回带引用标记的上下文文本（如 [1] 片段1\n\n[2] 片段2）及 sources 元数据列表。
        """
        try:
            has = self.pdf_ingest_service.has_data(character_id)
            logger.info("[RAG] character_id=%d, has_data=%s", character_id, has)
            if not has:
                return "", []
            rows = self.pdf_ingest_service.search_with_meta(character_id, question)
            logger.info("[RAG] character_id=%d, retrieved %d chunks", character_id, len(rows))
            for i, row in enumerate(rows, 1):
                txt = str(row.get("text", ""))[:100]
                method = row.get("method", "unknown")
                hybrid_sc = round(float(row.get("hybrid_score", 0.0)), 4)
                rerank_sc = round(float(row.get("rerank_score", 0.0)), 4)
                src = row.get("source_file", "")
                logger.info("  [%d] method=%-8s hybrid=%.4f rerank=%.4f src=%s text=%s...", i, method, hybrid_sc, rerank_sc, src, txt)
            if rows:
                context_parts = []
                for i, row in enumerate(rows, 1):
                    text = row.get("text", "")
                    context_parts.append(f"[{i}] {text}")
                context_text = "\n\n".join(context_parts)
                sources = [
                    {
                        "source_file": str(row.get("source_file", "")),
                        "chunk_index": int(row.get("chunk_index", 0)),
                        "score": round(float(row.get("hybrid_score", row.get("score", 0.0))), 4),
                        "text": str(row.get("text", "")),
                    }
                    for row in rows
                ]
                # Graph RAG 增强：如果该角色有知识图谱，追加图检索结果
                graph_ctx = self.graph_service.graph_context(character_id, question)
                if graph_ctx:
                    context_text = context_text + "\n\n" + graph_ctx
                return context_text, sources
        except Exception as e:
            logger.error("[RAG] retrieve error: %s", e, exc_info=True)
        return "", []

    @staticmethod
    def _blocked_pattern() -> re.Pattern | None:
        """构建屏蔽词的正则表达式模式（用于快速匹配）"""
        if not BLOCKED_WORDS:
            return None
        return re.compile("|".join(re.escape(w) for w in BLOCKED_WORDS), re.IGNORECASE)

    @staticmethod
    def _contains_blocked_word(text: str) -> bool:
        """检查文本中是否包含屏蔽词"""
        p = ChatService._blocked_pattern()
        return bool(p and p.search(text))

    @staticmethod
    def _filter_blocked_words(text: str) -> str:
        """如果文本包含屏蔽词，返回拒绝语；否则返回原文"""
        if ChatService._contains_blocked_word(text):
            return "抱歉，我无法回答这个问题"
        return text
