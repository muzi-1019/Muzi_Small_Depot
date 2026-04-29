"""
本文件的作用：管理后台 API 接口（仅管理员可访问）。
提供以下端点：
- GET /admin/stats         —— 获取系统统计数据（用户数、会话数、消息数等）
- GET /admin/users         —— 获取所有用户列表
- GET /admin/conversations —— 获取最近的会话列表
- GET /admin/knowledge     —— 获取最近的知识文档列表

所有接口都需要管理员权限（通过 require_admin 依赖注入校验）。
"""

from fastapi import APIRouter, Depends       # FastAPI 核心组件
from sqlalchemy import func, select          # SQLAlchemy 聚合函数和查询
from sqlalchemy.orm import Session           # 数据库会话

from app.db.session import get_db_session    # 获取数据库会话的依赖
from app.core.deps import require_admin      # 管理员权限校验依赖
from app.db.models import ChatMessage, Character, Conversation, KnowledgeDocument, User  # 数据库模型

router = APIRouter()  # 创建管理后台模块的路由器


@router.get("/stats")
def admin_stats(
    admin_id: int = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    """获取系统统计数据接口：返回用户数、会话数、消息数、角色数、知识文档数"""
    total_users = db.scalar(select(func.count(User.id))) or 0                    # 总用户数
    total_conversations = db.scalar(select(func.count(Conversation.id))) or 0    # 总会话数
    total_messages = db.scalar(select(func.count(ChatMessage.id))) or 0          # 总消息数
    total_characters = db.scalar(select(func.count(Character.id))) or 0          # 总角色数
    total_knowledge = db.scalar(select(func.count(KnowledgeDocument.id))) or 0   # 总知识文档数
    return {
        "total_users": total_users,
        "total_conversations": total_conversations,
        "total_messages": total_messages,
        "total_characters": total_characters,
        "total_knowledge": total_knowledge,
    }


@router.get("/users")
def admin_users(
    admin_id: int = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    """获取所有用户列表接口：返回用户ID、账号、昵称和注册时间"""
    rows = db.scalars(select(User).order_by(User.id.asc())).all()
    return [
        {
            "id": u.id,
            "account": u.account,
            "nickname": u.nickname,
            "created_at": u.created_at.isoformat() if u.created_at else "",
        }
        for u in rows
    ]


@router.get("/conversations")
def admin_conversations(
    admin_id: int = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    """获取最近50个会话列表接口（按最后更新时间倒序）"""
    rows = db.scalars(
        select(Conversation).order_by(Conversation.updated_at.desc()).limit(50)
    ).all()
    return [
        {
            "id": c.id,
            "user_id": c.user_id,
            "character_id": c.character_id,
            "title": c.title or "",
            "preview": c.preview or "",
            "updated_at": c.updated_at.isoformat() if c.updated_at else "",
        }
        for c in rows
    ]


@router.get("/knowledge")
def admin_knowledge(
    admin_id: int = Depends(require_admin),
    db: Session = Depends(get_db_session),
):
    """获取最近50个知识文档列表接口（按ID倒序）"""
    rows = db.scalars(
        select(KnowledgeDocument).order_by(KnowledgeDocument.id.desc()).limit(50)
    ).all()
    return [
        {
            "id": k.id,
            "character_id": k.character_id,
            "original_filename": k.original_filename,
            "status": k.status,
            "created_at": k.created_at.isoformat() if k.created_at else "",
        }
        for k in rows
    ]
