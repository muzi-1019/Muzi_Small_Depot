"""
本文件的作用：依赖注入工厂（Dependency Injection）。
FastAPI 框架的核心机制之一：每当一个 API 接口需要用到数据库、服务层对象时，
不是自己手动创建，而是通过 Depends() 自动调用这里的工厂函数来获取。
好处：解耦代码、方便测试、统一管理对象的创建方式。

本文件还包含两个鉴权函数：
- get_current_user_id：从请求头的 Token 中解析出当前登录用户的 ID
- require_admin：在 get_current_user_id 基础上额外检查是否是管理员
"""

from fastapi import Depends, HTTPException, status                    # FastAPI 的依赖注入装饰器和异常类
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # 从请求头提取 Bearer Token 的工具
from jose import JWTError, jwt                                        # JWT 解码工具
from sqlalchemy.orm import Session                                    # 数据库会话类型

from app.core.admins import ADMIN_ACCOUNTS                  # 管理员账号列表
from app.core.config import settings                        # 全局配置
from app.db.session import get_db_session                   # 数据库会话生成器
from app.repositories.character_repository import CharacterRepository      # 角色数据访问层
from app.repositories.conversation_repository import ConversationRepository  # 会话数据访问层
from app.repositories.knowledge_repository import KnowledgeRepository      # 知识库数据访问层
from app.repositories.user_repository import UserRepository                # 用户数据访问层
from app.services.chat_service import ChatService              # 聊天业务逻辑服务
from app.services.knowledge_service import KnowledgeService    # 知识库业务逻辑服务
from app.services.memory_service import MemoryService          # 对话记忆服务（Redis）
from app.services.pdf_ingest_service import PDFIngestService   # PDF 解析与向量入库服务

# HTTPBearer 会自动从请求头 Authorization: Bearer <token> 中提取 Token
security = HTTPBearer()


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """
    从请求头中的 JWT Token 解析出当前登录用户的 ID。
    每个需要登录才能访问的 API 接口都会依赖这个函数。
    如果 Token 无效或过期，会返回 401 未授权错误。
    """
    token = credentials.credentials  # 获取 Token 字符串
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])  # 用密钥解密 Token
        user_id: str = payload.get("sub")  # 从 Token 数据中取出用户 ID（sub 字段）
        if user_id is None:  # 如果 Token 中没有用户 ID，说明 Token 无效
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return int(user_id)  # 返回用户 ID（整数类型）
    except JWTError:  # Token 解密失败（过期、篡改等情况）
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_admin(
    current_user_id: int = Depends(get_current_user_id),  # 先获取当前用户 ID
    db: Session = Depends(get_db_session),                 # 再获取数据库连接
) -> int:
    """
    管理员权限验证。先验证用户登录，再检查该用户是否在管理员列表中。
    用于上传知识库、管理角色等高权限操作的 API 接口。
    """
    user = UserRepository(db=db).get_by_id(current_user_id)  # 从数据库查询用户信息
    if not user or user.account not in ADMIN_ACCOUNTS:  # 用户不存在或不在管理员列表中
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user_id  # 验证通过，返回管理员的用户 ID


# ==================== 以下是各种数据访问层和服务层的工厂函数 ====================
# FastAPI 通过 Depends() 调用这些函数，自动创建所需的对象并注入到 API 接口中

def get_character_repository(
    db: Session = Depends(get_db_session),
) -> CharacterRepository:
    """创建角色数据访问对象（负责角色的增删改查操作）"""
    return CharacterRepository(db=db)


def get_user_repository(db: Session = Depends(get_db_session)) -> UserRepository:
    """创建用户数据访问对象（负责用户的查询、注册等操作）"""
    return UserRepository(db=db)


def get_conversation_repository(db: Session = Depends(get_db_session)) -> ConversationRepository:
    """创建会话数据访问对象（负责对话记录的存取）"""
    return ConversationRepository(db=db)


def get_knowledge_repository(db: Session = Depends(get_db_session)) -> KnowledgeRepository:
    """创建知识库数据访问对象（负责知识文档记录的存取）"""
    return KnowledgeRepository(db=db)


def get_memory_service() -> MemoryService:
    """创建对话记忆服务（基于 Redis 存储短期对话上下文）"""
    return MemoryService()


def get_pdf_ingest_service() -> PDFIngestService:
    """创建 PDF 解析服务（负责 PDF 文本提取、切分、向量化、写入 Milvus）"""
    return PDFIngestService()


def get_knowledge_service(db: Session = Depends(get_db_session)) -> KnowledgeService:
    """创建知识库业务服务（负责文件上传、解析入库的完整流程）"""
    return KnowledgeService(KnowledgeRepository(db=db))


def get_chat_service(db: Session = Depends(get_db_session)) -> ChatService:
    """
    创建聊天业务服务（核心服务，负责接收用户问题、检索知识、调用大模型、返回回答）。
    需要组装多个依赖：角色仓库、用户仓库、会话仓库、记忆服务。
    """
    return ChatService(
        character_repository=CharacterRepository(db=db),
        user_repository=UserRepository(db=db),
        conversation_repository=ConversationRepository(db=db),
        memory_service=get_memory_service(),
    )
