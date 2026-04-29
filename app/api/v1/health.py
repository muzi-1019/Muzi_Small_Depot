"""
本文件的作用：健康检查 API 接口。
提供一个简单的 /health 端点，用于监控系统是否正常运行。
运维人员或负载均衡器可以定期调用此接口来确认服务是否存活。
"""

from fastapi import APIRouter  # FastAPI 路由器

router = APIRouter()  # 创建健康检查模块的路由器


@router.get("/health")
async def health_check():
    """健康检查接口：返回 {"status": "ok"} 表示服务正常运行"""
    return {"status": "ok"}
