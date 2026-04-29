"""
本文件的作用：知识库管理相关的 API 接口。
提供以下端点：
- POST /knowledge/upload —— 上传知识文件（仅管理员，文件会自动解析并写入向量库）
- GET  /knowledge/list   —— 查看指定角色的知识文档列表（已登录用户均可访问）
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile  # FastAPI 核心组件

from app.core.deps import get_knowledge_service, get_current_user_id, require_admin  # 依赖注入
from app.schemas.knowledge import KnowledgeItemOut, KnowledgeListResponse, KnowledgeUploadResponse  # 数据结构
from app.services.knowledge_service import KnowledgeService  # 知识库业务服务

router = APIRouter()  # 创建知识库模块的路由器


@router.post("/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge(
    character_id: int = Form(..., description="目标角色 id，用于知识库分区"),
    file: UploadFile = File(...),
    current_user_id: int = Depends(require_admin),  # 仅管理员可上传
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeUploadResponse:
    """上传知识文件接口：管理员上传文件后，系统自动解析并写入向量知识库"""
    doc = await service.ingest_upload(character_id=character_id, file=file)
    item = KnowledgeItemOut(
        id=doc.id,
        character_id=doc.character_id,
        original_filename=doc.original_filename,
        status=doc.status,
        created_at=doc.created_at,
    )
    return KnowledgeUploadResponse(data=item)


@router.get("/list", response_model=KnowledgeListResponse)
def list_knowledge(
    character_id: int,
    current_user_id: int = Depends(get_current_user_id),  # 需要登录
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeListResponse:
    """查看指定角色的知识文档列表接口"""
    rows = service.repository.list_by_character(character_id=character_id)
    data = [
        KnowledgeItemOut(
            id=r.id,
            character_id=r.character_id,
            original_filename=r.original_filename,
            status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return KnowledgeListResponse(data=data)
