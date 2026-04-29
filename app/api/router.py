"""
本文件的作用：API 路由总配置文件。
将所有 API 接口模块（认证、角色、聊天、知识库、管理后台、健康检查）
注册到统一的路由器中，并提供两个 URL 前缀版本：
- /api/...     (v0 版本，向后兼容)
- /api/v1/...  (v1 版本，推荐使用)

这样前端访问 /api/auth/login 或 /api/v1/auth/login 都能到达同一个接口。
"""

from fastapi import APIRouter  # FastAPI 的路由器类

from app.api.v1 import admin, auth, characters, chat, health, knowledge  # 导入各个 API 模块


def _mount_common_routes(router: APIRouter) -> None:
    """将所有子路由挂载到指定的父路由器上"""
    router.include_router(health.router, tags=["health"])              # 健康检查：/health
    router.include_router(auth.router, prefix="/auth", tags=["auth"])  # 认证：/auth/login, /auth/register 等
    router.include_router(characters.router, prefix="/characters", tags=["characters"])  # 角色管理：/characters
    router.include_router(chat.router, prefix="/chat", tags=["chat"])  # 聊天：/chat/send, /chat/stream 等
    router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])  # 知识库：/knowledge/upload
    router.include_router(admin.router, prefix="/admin", tags=["admin"])  # 管理后台：/admin/stats


# 创建总路由器
api_router = APIRouter()

# v0 版本路由：/api/...
api_router_v0 = APIRouter(prefix="/api")
_mount_common_routes(api_router_v0)
api_router.include_router(api_router_v0)

# v1 版本路由：/api/v1/...
api_router_v1 = APIRouter(prefix="/api/v1")
_mount_common_routes(api_router_v1)
api_router.include_router(api_router_v1)
