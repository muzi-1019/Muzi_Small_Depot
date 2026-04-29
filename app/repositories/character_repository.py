"""
本文件的作用：角色数据访问层（Repository）。
封装了所有与角色表（character）相关的数据库操作，包括：
- 查询角色列表、按ID查询角色
- 创建/更新/删除角色
- 删除角色时级联归档该角色下的所有会话和消息，并清理关联的知识文档记录
"""

from datetime import datetime  # 日期时间类型

from sqlalchemy import select        # SQLAlchemy 查询构造器
from sqlalchemy.orm import Session   # 数据库会话

from app.db.models import ArchivedChatMessage, ArchivedConversation, Character, ChatMessage, Conversation, KnowledgeDocument  # 数据库模型
from app.schemas.character import CharacterOut  # 角色输出格式


class CharacterRepository:
    """角色数据访问类：所有角色相关的数据库读写操作都在这里"""

    def __init__(self, db: Session) -> None:
        self.db = db  # 保存数据库会话

    @staticmethod
    def _to_schema(character: Character) -> CharacterOut:
        """将数据库 Character 模型对象转换为 API 输出格式"""
        return CharacterOut(
            id=character.id,
            name=character.name,
            role_type=character.role_type,
            domain=character.domain,
            persona=character.persona,
            prompt_template=character.prompt_template or "",
            knowledge_base_id=character.knowledge_base_id or "",
        )

    def list_characters(self) -> list[CharacterOut]:
        """获取所有角色列表，按ID升序排列"""
        rows = self.db.scalars(select(Character).order_by(Character.id.asc())).all()
        return [self._to_schema(row) for row in rows]

    def get_by_id(self, character_id: int) -> CharacterOut | None:
        """根据角色ID查询单个角色"""
        row = self.db.scalar(select(Character).where(Character.id == character_id))
        if not row:
            return None
        return self._to_schema(row)

    def create(self, *, name: str, role_type: str, domain: str, persona: str, prompt_template: str = "", knowledge_base_id: str = "") -> Character:
        """创建一个新角色并写入数据库"""
        row = Character(name=name, role_type=role_type, domain=domain, persona=persona, prompt_template=prompt_template, knowledge_base_id=knowledge_base_id)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, character_id: int, **kwargs) -> Character | None:
        """更新角色信息，只更新传入的非 None 字段"""
        row = self.db.scalar(select(Character).where(Character.id == character_id))
        if not row:
            return None
        for k, v in kwargs.items():
            if hasattr(row, k) and v is not None:
                setattr(row, k, v)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, character_id: int) -> bool:
        """
        删除角色（级联操作）：
        1. 将该角色下所有会话和消息归档到备份表
        2. 删除该角色的知识文档记录
        3. 最后删除角色本身
        """
        row = self.db.scalar(select(Character).where(Character.id == character_id))
        if not row:
            return False
        now = datetime.now()
        convs = self.db.scalars(
            select(Conversation).where(Conversation.character_id == character_id)
        ).all()
        for conv in convs:
            self.db.add(ArchivedConversation(
                original_conversation_id=conv.id,
                user_id=conv.user_id,
                character_id=conv.character_id,
                title=conv.title or "",
                preview=conv.preview or "",
                created_at=conv.created_at,
                archived_at=now,
            ))
            msgs = self.db.scalars(
                select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
            ).all()
            for m in msgs:
                self.db.add(ArchivedChatMessage(
                    original_conversation_id=conv.id,
                    user_message=m.user_message,
                    ai_reply=m.ai_reply,
                    created_at=m.created_at,
                    archived_at=now,
                ))
        conv_ids = [c.id for c in convs]
        if conv_ids:
            self.db.execute(
                ChatMessage.__table__.delete().where(ChatMessage.conversation_id.in_(conv_ids))
            )
            self.db.execute(
                Conversation.__table__.delete().where(Conversation.character_id == character_id)
            )
        self.db.execute(
            KnowledgeDocument.__table__.delete().where(KnowledgeDocument.character_id == character_id)
        )
        self.db.delete(row)
        self.db.commit()
        return True
