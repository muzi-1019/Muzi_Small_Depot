"""
本文件的作用：聊天相关的 API 接口（系统最主要的接口文件）。
提供以下端点：
- POST /chat/send      —— 发送消息（非流式，一次性返回完整回复）
- POST /chat/stream    —— 发送消息（流式，逐字返回，打字机效果）
- GET  /chat/conversations —— 获取用户的会话列表
- POST /chat/conversations —— 创建新会话
- DELETE /chat/conversations/{id} —— 删除会话
- PATCH  /chat/conversations/{id} —— 重命名会话
- GET  /chat/history   —— 获取指定会话的历史消息
- GET  /chat/export    —— 导出对话为 Markdown 文件
- GET  /chat/search    —— 搜索消息内容

所有接口都需要 JWT 认证，且只允许操作自己的数据。
"""

import json  # JSON 序列化（用于 SSE 错误输出）

from fastapi import APIRouter, Depends, HTTPException, Query, Request  # FastAPI 核心组件
from fastapi.responses import PlainTextResponse, StreamingResponse    # 特殊响应类型

from app.core.deps import get_chat_service, get_conversation_repository, get_current_user_id  # 依赖注入
from app.repositories.conversation_repository import ConversationRepository  # 会话数据访问层
from app.schemas.chat import ChatRequest, ChatResponse, ConversationListResponse, ConversationResponse, HistoryResponse, RenameConversationRequest  # 数据结构
from app.services.chat_service import ChatService  # 聊天业务服务

router = APIRouter()  # 创建聊天模块的路由器


@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse, include_in_schema=False)
@router.post("/send", response_model=ChatResponse)
@router.post("/send/", response_model=ChatResponse, include_in_schema=False)
def send_chat(
    payload: ChatRequest,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    """非流式聊天接口：前端发送问题，后端一次性返回完整的 AI 回复"""
    if payload.user_id != current_user_id:  # 权限校验：只能操作自己的对话
        raise HTTPException(status_code=403, detail="无权操作其他用户的对话")
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else None)
    return chat_service.send_message(payload, client_ip=client_ip)


@router.post("/stream")
@router.post("/stream/", include_in_schema=False)
def stream_chat(
    payload: ChatRequest,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
):
    """流式聊天接口：前端发送问题，后端通过 SSE 逐字返回 AI 回复（打字机效果）"""
    if payload.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权操作其他用户的对话")
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else None)

    def _safe_stream():
        """包装流式生成器，捕获并记录内部异常"""
        try:
            yield from chat_service.send_message_stream(payload, client_ip=client_ip)
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).exception("流式聊天异常")
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _safe_stream(),
        media_type="text/event-stream",              # SSE 内容类型
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # 禁用缓存和反向代理缓冲
    )


@router.get("/conversations", response_model=ConversationListResponse)
def get_conversations(
    user_id: int = Query(..., description="注册用户 id"),
    character_id: int | None = Query(None, description="角色 id（可选）"),
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> ConversationListResponse:
    """获取用户的会话列表接口（可按角色ID过滤）"""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权查看其他用户的对话列表")
    return chat_service.list_conversations(user_id=user_id, character_id=character_id)


@router.post("/conversations", response_model=ConversationResponse)
@router.post("/conversations/", response_model=ConversationResponse, include_in_schema=False)
def create_conversation(
    payload: ChatRequest,
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> ConversationResponse:
    """手动创建新会话接口"""
    if payload.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权创建其他用户的对话")
    return chat_service.create_conversation(payload.user_id, payload.character_id, payload.question or "新对话")


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user_id: int = Query(..., description="注册用户 id"),
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
):
    """删除指定会话接口（会话数据会自动归档到备份表）"""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权删除其他用户的对话")
    return chat_service.delete_conversation(user_id=user_id, conversation_id=conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
def rename_conversation(
    conversation_id: int,
    payload: RenameConversationRequest,
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> ConversationResponse:
    """重命名指定会话接口"""
    if payload.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权修改其他用户的对话")
    return chat_service.rename_conversation(user_id=payload.user_id, conversation_id=conversation_id, title=payload.title)


@router.get("/history", response_model=HistoryResponse)
def get_history(
    user_id: int = Query(..., description="注册用户 id"),
    conversation_id: int = Query(..., description="对话 id"),
    limit: int = Query(50, ge=1, le=200),
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> HistoryResponse:
    """获取指定会话的历史消息列表接口"""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权查看其他用户的历史记录")
    return chat_service.history(user_id=user_id, conversation_id=conversation_id, limit=limit)


@router.get("/export")
def export_conversation(
    user_id: int = Query(...),
    conversation_id: int = Query(...),
    current_user_id: int = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
):
    """导出对话为 Markdown 文件接口（浏览器会自动下载）"""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权导出其他用户的对话")
    md = chat_service.export_conversation(user_id=user_id, conversation_id=conversation_id)
    return PlainTextResponse(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=conversation_{conversation_id}.md"},
    )


@router.get("/search")
def search_messages(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(30, ge=1, le=100),
    current_user_id: int = Depends(get_current_user_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
):
    """搜索消息接口：在当前用户的所有对话中搜索包含关键词的消息"""
    results = repo.search_messages(user_id=current_user_id, keyword=keyword, limit=limit)
    return {"code": 200, "data": results}

