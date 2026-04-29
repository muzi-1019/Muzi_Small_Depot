"""
本文件的作用：定义知识库相关 API 的请求和响应数据结构。
包括知识文档的输出格式、文档列表响应、上传响应等。
"""

from datetime import datetime  # 日期时间类型

from pydantic import BaseModel, Field  # Pydantic 数据模型基类


class KnowledgeItemOut(BaseModel):
    """单个知识文档的输出格式"""
    id: int                    # 文档记录ID
    character_id: int          # 所属角色ID
    original_filename: str     # 原始文件名
    status: str                # 处理状态：pending / processed / failed
    created_at: datetime       # 上传时间


class KnowledgeListResponse(BaseModel):
    """知识文档列表响应"""
    code: int = 200
    message: str = "success"
    data: list[KnowledgeItemOut]  # 文档数组


class KnowledgeUploadResponse(BaseModel):
    """知识文档上传响应"""
    code: int = 200
    message: str = "success"
    data: KnowledgeItemOut        # 上传成功的文档信息
