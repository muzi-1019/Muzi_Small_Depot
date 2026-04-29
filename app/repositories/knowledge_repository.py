"""
本文件的作用：知识文档数据访问层（Repository）。
封装了与知识文档表（knowledge_document）相关的数据库操作：
- 创建知识文档记录（上传文件时记录元信息）
- 按角色ID查询该角色关联的所有知识文档
"""

from sqlalchemy import select        # SQLAlchemy 查询构造器
from sqlalchemy.orm import Session   # 数据库会话

from app.db.models import KnowledgeDocument  # 知识文档数据库模型


class KnowledgeRepository:
    """知识文档数据访问类"""

    def __init__(self, db: Session) -> None:
        self.db = db  # 保存数据库会话

    def create(
        self,
        character_id: int,        # 所属角色ID
        original_filename: str,   # 原始文件名
        stored_path: str,         # 服务器存储路径
        content_type: str,        # 文件MIME类型
        status: str = "pending",  # 初始处理状态
    ) -> KnowledgeDocument:
        """创建一条知识文档记录（文件上传后调用，记录文件元信息）"""
        row = KnowledgeDocument(
            character_id=character_id,
            original_filename=original_filename,
            stored_path=stored_path,
            content_type=content_type,
            status=status,
        )
        self.db.add(row)       # 添加到数据库会话
        self.db.commit()       # 提交事务
        self.db.refresh(row)   # 刷新获取自增ID
        return row

    def list_by_character(self, character_id: int, limit: int = 100) -> list[KnowledgeDocument]:
        """查询指定角色的所有知识文档，按ID倒序排列（最新上传的排在前面）"""
        return list(
            self.db.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.character_id == character_id)
                .order_by(KnowledgeDocument.id.desc())
                .limit(limit)
            ).all()
        )
