"""
本文件的作用：知识库业务服务。
负责处理知识文件的上传流程：
1. 校验文件格式（仅支持 txt/pdf/md）
2. 将文件保存到服务器磁盘
3. 在数据库中创建文档记录
4. 如果是 PDF 文件，自动调用 PDF 解析服务提取文本、切分、向量化并写入 Milvus
"""

import uuid             # 用于生成唯一文件名，避免文件名冲突
from pathlib import Path  # 文件路径处理工具

from fastapi import HTTPException, UploadFile  # HTTP 异常和上传文件类型

from app.core.config import settings                           # 全局配置
from app.db.models import KnowledgeDocument                    # 知识文档数据库模型
from app.repositories.knowledge_repository import KnowledgeRepository  # 知识文档数据访问层
from app.services.pdf_ingest_service import PDFIngestService   # PDF 解析与向量入库服务


class KnowledgeService:
    """知识库业务服务：管理知识文件的上传和入库流程"""

    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository                    # 数据库操作层
        self.pdf_ingest_service = PDFIngestService()    # PDF 解析服务

    async def ingest_upload(self, character_id: int, file: UploadFile) -> KnowledgeDocument:
        """
        处理知识文件上传的完整流程。
        参数：character_id=目标角色ID，file=上传的文件对象
        返回：创建的知识文档记录
        """
        raw_name = file.filename or "upload.bin"           # 获取原始文件名
        suffix = Path(raw_name).suffix.lower()             # 获取文件扩展名
        if suffix not in {".txt", ".pdf", ".md"}:          # 校验文件格式
            raise HTTPException(status_code=400, detail="仅支持 txt、pdf、md 文件")

        # 创建存储目录并保存文件
        base_dir = Path(settings.upload_dir) / str(character_id)  # 按角色ID分目录存储
        base_dir.mkdir(parents=True, exist_ok=True)                # 目录不存在则自动创建
        stored = f"{uuid.uuid4().hex}{suffix}"                     # 生成唯一文件名
        dest = base_dir / stored                                   # 完整存储路径
        body = await file.read()                                   # 读取上传文件内容
        if not body:
            raise HTTPException(status_code=400, detail="空文件")
        dest.write_bytes(body)                                     # 写入磁盘

        # 在数据库中创建文档记录
        doc = self.repository.create(
            character_id=character_id,
            original_filename=raw_name,
            stored_path=str(dest.resolve()),
            content_type=file.content_type or "application/octet-stream",
            status="pending",   # 初始状态为待处理
        )

        # 如果是 PDF 文件，自动解析并写入向量库
        if suffix == ".pdf":
            try:
                self.pdf_ingest_service.ingest_file(character_id, dest.resolve())  # 解析 PDF → 切分 → 向量化 → 写入 Milvus
                doc.status = "processed"         # 处理成功
                self.repository.db.commit()
                self.repository.db.refresh(doc)
            except Exception as exc:
                doc.status = "failed"            # 处理失败
                doc.error_message = str(exc)     # 记录错误信息
                self.repository.db.commit()
                self.repository.db.refresh(doc)

        return doc
