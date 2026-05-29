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
            id=character.id,  # 角色 ID
            name=character.name,  # 角色名称
            role_type=character.role_type,  # 角色类型
            domain=character.domain,  # 角色领域
            persona=character.persona,  # 角色人设描述
            prompt_template=character.prompt_template or "",  # 提示词模板，空值转为空字符串
            knowledge_base_id=character.knowledge_base_id or "",  # 知识库标识，空值转为空字符串
        )  # 返回 API 层使用的 Pydantic 结构

    def list_characters(self) -> list[CharacterOut]:
        """获取所有角色列表，按ID升序排列"""
        rows = self.db.scalars(select(Character).order_by(Character.id.asc())).all()  # 查询所有角色并按 ID 升序排列
        return [self._to_schema(row) for row in rows]  # 将 ORM 对象列表转换为响应模型列表

    def get_by_id(self, character_id: int) -> CharacterOut | None:
        """根据角色ID查询单个角色"""
        row = self.db.scalar(select(Character).where(Character.id == character_id))  # 按角色 ID 查询数据库记录
        if not row:  # 如果没有找到角色
            return None  # 返回 None 给上层处理 404
        return self._to_schema(row)  # 找到后转换为输出结构

    def create(self, *, name: str, role_type: str, domain: str, persona: str, prompt_template: str = "", knowledge_base_id: str = "") -> Character:
        """创建一个新角色并写入数据库"""
        row = Character(name=name, role_type=role_type, domain=domain, persona=persona, prompt_template=prompt_template, knowledge_base_id=knowledge_base_id)  # 构造角色 ORM 对象
        self.db.add(row)  # 加入当前数据库会话
        self.db.commit()  # 提交事务，写入数据库
        self.db.refresh(row)  # 刷新对象，获取自增 ID 等数据库生成字段
        return row  # 返回新创建的角色对象

    def update(self, character_id: int, **kwargs) -> Character | None:
        """更新角色信息，只更新传入的非 None 字段"""
        row = self.db.scalar(select(Character).where(Character.id == character_id))  # 查询要更新的角色
        if not row:  # 如果角色不存在
            return None  # 返回 None
        for k, v in kwargs.items():  # 遍历调用方传入的字段和值
            if hasattr(row, k) and v is not None:  # 只更新模型存在且值非 None 的字段
                setattr(row, k, v)  # 动态设置字段值
        self.db.commit()  # 提交更新
        self.db.refresh(row)  # 刷新对象，确保返回最新数据
        return row  # 返回更新后的角色对象

    def delete(self, character_id: int) -> bool:
        """
        删除角色（级联操作）：
        1. 将该角色下所有会话和消息归档到备份表
        2. 删除该角色的知识文档记录
        3. 最后删除角色本身
        """
        row = self.db.scalar(select(Character).where(Character.id == character_id))  # 查询待删除角色
        if not row:  # 如果角色不存在
            return False  # 返回 False 表示删除失败
        now = datetime.now()  # 统一记录本次归档时间
        convs = self.db.scalars(
            select(Conversation).where(Conversation.character_id == character_id)  # 查询该角色下所有会话
        ).all()  # 执行查询并取出全部会话
        for conv in convs:  # 遍历每个会话，逐个归档
            self.db.add(ArchivedConversation(
                original_conversation_id=conv.id,  # 保存原始会话 ID
                user_id=conv.user_id,  # 保存会话所属用户
                character_id=conv.character_id,  # 保存会话所属角色
                title=conv.title or "",  # 保存原会话标题
                preview=conv.preview or "",  # 保存原会话预览
                created_at=conv.created_at,  # 保存原创建时间
                archived_at=now,  # 保存归档时间
            ))  # 添加归档会话记录
            msgs = self.db.scalars(
                select(ChatMessage).where(ChatMessage.conversation_id == conv.id)  # 查询该会话下所有消息
            ).all()  # 执行消息查询
            for m in msgs:  # 遍历消息并归档
                self.db.add(ArchivedChatMessage(
                    original_conversation_id=conv.id,  # 保存消息所属原会话 ID
                    user_message=m.user_message,  # 保存用户原始消息
                    ai_reply=m.ai_reply,  # 保存 AI 原始回复
                    created_at=m.created_at,  # 保存消息原创建时间
                    archived_at=now,  # 保存归档时间
                ))  # 添加归档消息记录
        conv_ids = [c.id for c in convs]  # 收集该角色所有会话 ID
        if conv_ids:  # 如果该角色确实有会话
            self.db.execute(
                ChatMessage.__table__.delete().where(ChatMessage.conversation_id.in_(conv_ids))  # 删除这些会话下的原始消息
            )  # 执行消息删除
            self.db.execute(
                Conversation.__table__.delete().where(Conversation.character_id == character_id)  # 删除该角色的原始会话
            )  # 执行会话删除
        self.db.execute(
            KnowledgeDocument.__table__.delete().where(KnowledgeDocument.character_id == character_id)  # 删除该角色关联的知识文档记录
        )  # 执行知识文档记录删除
        self.db.delete(row)  # 删除角色本身
        self.db.commit()  # 提交所有归档和删除操作
        return True  # 返回删除成功
