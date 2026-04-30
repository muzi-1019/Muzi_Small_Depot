"""
本文件的作用：定义聊天相关 API 的请求和响应数据结构（Schema / DTO）。
这些类不直接操作数据库，而是用于：
1. 校验前端发来的请求参数格式是否正确（如必填字段、类型检查）
2. 定义后端返回给前端的 JSON 数据结构（保证接口文档与实际数据一致）
Pydantic 的 BaseModel 会自动完成类型校验和序列化。
"""

from datetime import datetime  # 日期时间类型

from pydantic import BaseModel, Field  # Pydantic 数据模型基类和字段约束


class ChatRequest(BaseModel):
    """聊天请求：前端发送消息时提交的数据格式"""
    user_id: int                            # 当前登录用户的ID
    character_id: int                       # 正在对话的角色ID
    question: str                           # 用户提出的问题/消息内容
    conversation_id: int | None = None      # 所属会话ID（如果为空则自动创建新会话）
    latitude: float | None = None           # 用户地理位置纬度（由前端浏览器定位获取，可选）
    longitude: float | None = None          # 用户地理位置经度（由前端浏览器定位获取，可选）


class RetrievedSource(BaseModel):
    """检索到的知识片段：用于参考文献展示"""
    source_file: str                        # 来源 PDF 文件名
    chunk_index: int = 0                    # 片段编号
    score: float = 0.0                      # 相似度得分
    text: str                               # 片段原文


class ChatData(BaseModel):
    """聊天响应数据：非流式回复时，AI 回答的具体内容"""
    answer: str                             # AI 的回复文本
    retrieve_knowledge: list[RetrievedSource] = []  # 检索到的知识片段（参考文献）
    rag_used: bool = False                  # 本次回复是否使用了向量知识库检索


class ChatResponse(BaseModel):
    """聊天响应：非流式回复的完整 JSON 结构"""
    code: int = 200                         # 状态码（200 表示成功）
    message: str = "success"                # 状态消息
    data: ChatData                          # 回复数据


class HistoryItem(BaseModel):
    """单条历史消息：包含一问一答及参考文献"""
    user_message: str                       # 用户发送的消息
    ai_reply: str                           # AI 的回复
    rag_used: bool = False                  # 该回复是否使用了向量知识库检索
    sources: list[RetrievedSource] = []     # 检索到的参考文献列表
    created_at: datetime                    # 消息创建时间


class HistoryResponse(BaseModel):
    """历史消息列表响应"""
    code: int = 200
    message: str = "success"
    data: list[HistoryItem]                 # 历史消息数组


class ConversationItem(BaseModel):
    """单个会话信息：显示在左侧会话列表中"""
    id: int                                 # 会话ID
    user_id: int                            # 所属用户ID
    character_id: int                       # 对话的角色ID
    title: str                              # 会话标题
    preview: str                            # 最新消息预览
    created_at: datetime                    # 创建时间
    updated_at: datetime                    # 最后更新时间


class ConversationListResponse(BaseModel):
    """会话列表响应"""
    code: int = 200
    message: str = "success"
    data: list[ConversationItem]            # 会话数组


class ConversationResponse(BaseModel):
    """单个会话操作响应（创建/重命名等）"""
    code: int = 200
    message: str = "success"
    data: ConversationItem                  # 操作后的会话信息


class RenameConversationRequest(BaseModel):
    """重命名会话请求"""
    user_id: int                            # 用户ID
    title: str = Field(..., min_length=1, max_length=128, examples=["我的对话"])  # 新标题（1~128 字符）
