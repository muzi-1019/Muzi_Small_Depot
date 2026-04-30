"""
本文件的作用：对话/会话数据访问层（Repository）。
封装了所有与对话（conversation）和聊天消息（chat_message）相关的数据库操作，包括：
- 创建/查询/更新/删除会话
- 添加/查询/搜索聊天消息
- 删除会话时自动归档到备份表（防止数据丢失）
"""

import json
from datetime import datetime  # 日期时间类型

from sqlalchemy import desc, or_, select  # desc=降序排列, or_=SQL OR 条件, select=查询构造器
from sqlalchemy.orm import Session        # 数据库会话

from app.db.models import ArchivedChatMessage, ArchivedConversation, ChatMessage, Conversation  # 数据库模型


class ConversationRepository:
    """对话数据访问类：所有会话和消息相关的数据库读写操作都在这里"""

    def __init__(self, db: Session) -> None:
        self.db = db  # 保存数据库会话

    def create_conversation(self, user_id: int, character_id: int, title: str = "新对话") -> Conversation:
        """创建一个新的对话会话（用户首次向某角色发消息时自动创建）"""
        now = datetime.now()
        row = Conversation(user_id=user_id, character_id=character_id, title=title, preview="", created_at=now, updated_at=now)
        self.db.add(row)        # 添加到会话
        self.db.commit()        # 提交到数据库
        self.db.refresh(row)    # 刷新获取自增ID
        return row

    def list_conversations(self, user_id: int, character_id: int | None = None) -> list[Conversation]:
        """查询用户的所有会话列表（可选按角色ID过滤），按最后更新时间倒序排列"""
        stmt = select(Conversation).where(Conversation.user_id == user_id)
        if character_id:  # 如果指定了角色ID，只返回该角色的会话
            stmt = stmt.where(Conversation.character_id == character_id)
        rows = self.db.scalars(stmt.order_by(desc(Conversation.updated_at))).all()  # 按更新时间倒序
        return list(rows)

    def get_by_id(self, conversation_id: int) -> Conversation | None:
        """根据会话ID查询单个会话"""
        return self.db.get(Conversation, conversation_id)

    def update_conversation(self, conversation_id: int, title: str | None = None, preview: str | None = None) -> Conversation | None:
        """更新会话的标题和/或预览内容，同时更新最后修改时间"""
        conv = self.db.get(Conversation, conversation_id)
        if not conv:
            return None
        if title is not None:    # 如果传了新标题就更新
            conv.title = title
        if preview is not None:  # 如果传了新预览就更新
            conv.preview = preview
        conv.updated_at = datetime.now()  # 更新修改时间
        self.db.commit()
        self.db.refresh(conv)
        return conv

    def delete_conversation(self, conversation_id: int) -> bool:
        """
        删除会话（软删除）：
        1. 先将会话信息和所有消息备份到归档表
        2. 再从原表中删除
        这样即使误删也能从归档表恢复数据。
        """
        conv = self.db.get(Conversation, conversation_id)
        if not conv:
            return False
        now = datetime.now()
        # 将会话信息归档
        self.db.add(ArchivedConversation(
            original_conversation_id=conv.id,
            user_id=conv.user_id,
            character_id=conv.character_id,
            title=conv.title or "",
            preview=conv.preview or "",
            created_at=conv.created_at,
            archived_at=now,
        ))
        # 将该会话下所有消息归档
        msgs = self.db.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        ).all()
        for m in msgs:
            self.db.add(ArchivedChatMessage(
                original_conversation_id=conversation_id,
                user_message=m.user_message,
                ai_reply=m.ai_reply,
                sources_json=m.sources_json or "",
                created_at=m.created_at,
                archived_at=now,
            ))
        # 从原表删除消息和会话
        self.db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
        self.db.delete(conv)
        self.db.commit()
        return True

    def add_message(self, conversation_id: int, user_message: str, ai_reply: str, rag_used: bool = False, sources: list[dict] | None = None) -> ChatMessage:
        """
        向指定会话添加一条消息（一问一答）。
        同时更新会话的预览内容和最后修改时间。
        sources 为检索到的知识片段列表，自动序列化为 JSON 存储。
        """
        conv = self.db.get(Conversation, conversation_id)
        if conv:
            conv.preview = user_message[:120]    # 用用户消息前120字作为预览
            conv.updated_at = datetime.now()
        msg = ChatMessage(
            conversation_id=conversation_id,
            user_message=user_message,        # 用户发送的消息
            ai_reply=ai_reply,                # AI 的回复
            rag_used=rag_used,                # 是否使用了向量知识库检索
            sources_json=json.dumps(sources or [], ensure_ascii=False),
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    def list_messages(self, conversation_id: int, limit: int = 50) -> list[ChatMessage]:
        """查询指定会话的最近 N 条消息，按时间正序返回（从旧到新）"""
        rows = self.db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())  # 先按时间倒序取最新的 N 条
            .limit(limit)
        ).all()
        return list(reversed(rows))  # 再反转为正序（从旧到新），方便前端按顺序显示

    def search_messages(self, user_id: int, keyword: str, limit: int = 50) -> list[dict]:
        """
        全文搜索：在用户的所有对话消息中搜索包含关键词的消息。
        同时搜索用户消息和 AI 回复，返回匹配的消息列表。
        """
        conv_ids_stmt = select(Conversation.id).where(Conversation.user_id == user_id)  # 先获取用户所有会话ID
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id.in_(conv_ids_stmt),  # 限制在用户自己的会话内搜索
                or_(
                    ChatMessage.user_message.contains(keyword),  # 用户消息包含关键词
                    ChatMessage.ai_reply.contains(keyword),      # 或 AI 回复包含关键词
                ),
            )
            .order_by(ChatMessage.created_at.desc())  # 按时间倒序
            .limit(limit)
        )
        rows = self.db.scalars(stmt).all()
        results = []
        for msg in rows:
            results.append({
                "message_id": msg.id,
                "conversation_id": msg.conversation_id,
                "user_message": msg.user_message,
                "ai_reply": msg.ai_reply,
                "created_at": msg.created_at.isoformat() if msg.created_at else "",
            })
        return results
