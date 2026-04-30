"""
本文件的作用：定义所有数据库表的结构（ORM 模型）。
每个 class 对应 MySQL 中的一张表，class 中的每个属性对应表的一个字段（列）。
SQLAlchemy ORM 会自动把 Python 对象和数据库表之间进行映射：
- 写入时：把 Python 对象自动转换为 SQL INSERT 语句
- 读取时：把查询结果自动转换为 Python 对象

本文件定义了以下 6 张表：
1. User（用户表）—— 存储注册用户信息
2. Character（角色表）—— 存储 AI 角色信息（如虚拟朋友、医生、律师等）
3. Conversation（对话表）—— 存储用户与角色之间的对话会话
4. ChatMessage（消息表）—— 存储每条具体的对话消息（一问一答）
5. ArchivedConversation / ArchivedChatMessage（归档表）—— 删除对话时的备份
6. KnowledgeDocument（知识文档表）—— 记录上传的 PDF/文档文件信息
"""

from datetime import datetime  # 日期时间类型，用于记录创建时间等

from sqlalchemy import DateTime, ForeignKey, String, Text  # SQLAlchemy 字段类型定义
from sqlalchemy.orm import Mapped, mapped_column            # SQLAlchemy 的类型映射装饰器

from app.db.base import Base  # 导入基类，所有模型都要继承它


class User(Base):
    """用户表：存储系统中所有注册用户的信息"""
    __tablename__ = "user"  # 对应 MySQL 中的表名

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)         # 用户唯一ID，自增主键
    account: Mapped[str] = mapped_column(String(64), unique=True, index=True)     # 账号（手机号或用户名），唯一且建立索引加速查询
    password_hash: Mapped[str] = mapped_column(String(255))                       # 加密后的密码哈希值（不存明文密码）
    nickname: Mapped[str] = mapped_column(String(64))                             # 用户昵称
    preferred_character_ids: Mapped[str] = mapped_column(
        Text, default="[]"
    )  # 用户偏好的角色ID列表，以 JSON 数组字符串存储，例如 "[1,2]"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 注册时间


class Character(Base):
    """角色表：存储所有 AI 角色的信息（每个角色有不同的性格、领域、知识库）"""
    __tablename__ = "character"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)      # 角色唯一ID
    name: Mapped[str] = mapped_column(String(64), index=True)                  # 角色名称，如"高血压专科医生"
    role_type: Mapped[str] = mapped_column(String(32), index=True)             # 角色类型：social（社交）/ professional（专业）/ custom（自定义）
    domain: Mapped[str] = mapped_column(String(64), index=True)                # 角色所属领域，如"医疗"、"法律"
    persona: Mapped[str] = mapped_column(Text)                                 # 角色人设描述，如"严谨、专业、注重风险提示"
    prompt_template: Mapped[str] = mapped_column(Text, default="")             # 角色专属的提示词模板，用于引导大模型的回答风格
    knowledge_base_id: Mapped[str] = mapped_column(String(64), default="")     # 关联的知识库标识
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 创建时间


class Conversation(Base):
    """对话（会话）表：记录用户与某个角色之间的一次对话会话（一个会话内可以有多条消息）"""
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)                  # 会话唯一ID
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)                # 所属用户ID（外键关联用户表）
    character_id: Mapped[int] = mapped_column(ForeignKey("character.id"), index=True)      # 对话的角色ID（外键关联角色表）
    title: Mapped[str] = mapped_column(String(128), default="新对话")                       # 会话标题（默认取用户第一条消息的前几个字）
    preview: Mapped[str] = mapped_column(Text, default="")                                 # 最新消息预览（显示在会话列表中）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)            # 创建时间
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)            # 最后更新时间（每次发消息都会更新）


class ChatMessage(Base):
    """聊天消息表：存储每一条具体的对话消息（包含用户提问和 AI 回复）"""
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)                  # 消息唯一ID
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id"), index=True)  # 所属会话ID（外键关联会话表）
    user_message: Mapped[str] = mapped_column(Text)                                        # 用户发送的消息内容
    ai_reply: Mapped[str] = mapped_column(Text)                                            # AI 的回复内容
    rag_used: Mapped[bool] = mapped_column(default=False)                                  # 这条回复是否使用了向量知识库检索（True=用了RAG, False=纯大模型）
    sources_json: Mapped[str] = mapped_column(Text, default="")                            # 检索到的知识片段元数据（JSON数组），包含来源文件、片段编号、相似度、原文
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)            # 消息创建时间


class ArchivedConversation(Base):
    """归档会话表：用户删除对话时，原始会话信息会备份到这里（防止误删丢失数据）"""
    __tablename__ = "archived_conversation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)       # 归档记录ID
    original_conversation_id: Mapped[int] = mapped_column(index=True)           # 原始会话ID
    user_id: Mapped[int] = mapped_column(index=True)                            # 原始用户ID
    character_id: Mapped[int] = mapped_column(index=True)                       # 原始角色ID
    title: Mapped[str] = mapped_column(String(128), default="")                 # 原始标题
    preview: Mapped[str] = mapped_column(Text, default="")                      # 原始预览
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 原始创建时间
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 归档（删除）时间


class ArchivedChatMessage(Base):
    """归档消息表：删除对话时，该对话下的所有消息会备份到这里"""
    __tablename__ = "archived_chat_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)       # 归档消息ID
    original_conversation_id: Mapped[int] = mapped_column(index=True)           # 原始会话ID
    user_message: Mapped[str] = mapped_column(Text)                             # 原始用户消息
    ai_reply: Mapped[str] = mapped_column(Text)                                 # 原始AI回复
    sources_json: Mapped[str] = mapped_column(Text, default="")                 # 原始检索知识片段元数据
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 原始创建时间
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)  # 归档时间


class KnowledgeDocument(Base):
    """知识文档表：记录每个角色上传的 PDF/TXT/MD 文件的元信息"""
    __tablename__ = "knowledge_document"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)                                # 文档记录ID
    character_id: Mapped[int] = mapped_column(ForeignKey("character.id"), index=True)                    # 所属角色ID
    original_filename: Mapped[str] = mapped_column(String(255))                                          # 原始文件名（用户上传时的文件名）
    stored_path: Mapped[str] = mapped_column(String(512))                                                # 服务器上的存储路径
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")           # 文件MIME类型
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)                       # 处理状态：pending（待处理）/ processed（已入库）/ failed（失败）
    error_message: Mapped[str] = mapped_column(Text, default="")                                         # 处理失败时的错误信息
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)                          # 上传时间
