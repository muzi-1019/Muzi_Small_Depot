"""
本文件的作用：FastAPI 应用的主入口文件。
负责创建和配置整个 Web 应用：
1. 创建 FastAPI 应用实例
2. 配置 CORS 跨域中间件（允许前端跨域请求后端 API）
3. 添加请求日志中间件（记录每个 HTTP 请求的耗时和状态）
4. 注册 API 路由
5. 托管前端静态文件（如果 frontend/dist 目录存在）
6. 应用启动时自动初始化数据库
"""

import logging             # 日志模块
import time                # 计时工具
from pathlib import Path   # 文件路径处理

from fastapi import FastAPI, Request                        # FastAPI 框架核心
from fastapi.middleware.cors import CORSMiddleware          # 跨域资源共享中间件
from fastapi.responses import FileResponse                  # 文件响应
from fastapi.staticfiles import StaticFiles                 # 静态文件托管

from app.api.router import api_router     # API 路由总配置
from app.core.config import settings      # 全局配置
from app.db.init_db import init_db        # 数据库初始化函数


class AccessLogMiddleware:
    """
    HTTP 请求日志中间件。
    记录每个 HTTP 请求的状态码、请求方法、路径、耗时和客户端 IP。
    方便开发调试和线上问题排查。
    """

    def __init__(self, app: FastAPI):
        self.app = app  # 保存 FastAPI 应用实例

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # 只处理 HTTP 请求，跳过 WebSocket 等
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        start = time.perf_counter()     # 记录请求开始时间
        status_code = 500               # 默认状态码（如果出错）

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]  # 捕获实际响应状态码
            await send(message)

        await self.app(scope, receive, send_wrapper)  # 执行实际请求处理
        elapsed_ms = (time.perf_counter() - start) * 1000  # 计算耗时（毫秒）
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        # 输出日志：[状态码] 方法 路径 耗时 客户端IP
        logging.getLogger("app.access").info(
            "[%s] %s %s  %.0fms  %s",
            status_code,
            request.method,
            request.url.path,
            elapsed_ms,
            client_ip,
        )


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    app = FastAPI(
        title=settings.app_name,           # 应用名称
        version="0.1.0",                   # 版本号
        debug=settings.app_debug,          # 调试模式
        description="Hybrid architecture skeleton for RAG role-play system",
    )

    # 配置 CORS 跨域中间件（允许前端从不同端口/域名访问后端 API）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],                                   # 允许所有来源
        allow_origin_regex=settings.cors_allow_origin_regex,   # 正则匹配来源
        allow_credentials=True,                                # 允许携带 Cookie
        allow_methods=["*"],                                   # 允许所有 HTTP 方法
        allow_headers=["*"],                                   # 允许所有请求头
    )

    app.add_middleware(AccessLogMiddleware)  # 添加请求日志中间件
    app.include_router(api_router)          # 注册所有 API 路由

    # 托管前端静态文件（生产环境：前端打包后的 dist 目录）
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")  # JS/CSS 等资源文件
        app.mount("/static", StaticFiles(directory=str(frontend_dist)), name="static")   # 其他静态文件

        @app.get("/")
        def serve_index() -> FileResponse:
            """访问根路径时返回前端首页"""
            return FileResponse(frontend_dist / "index.html")

        @app.get("/{path:path}")
        def serve_spa(path: str) -> FileResponse:
            """
            SPA 路由兜底：
            - 如果请求的是 API 路径，返回 404
            - 如果请求的文件存在，直接返回该文件
            - 否则返回 index.html（前端路由接管）
            """
            if path.startswith("api/") or path.startswith("api"):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            file_path = frontend_dist / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(frontend_dist / "index.html")

    @app.on_event("startup")
    def on_startup() -> None:
        """应用启动时自动初始化数据库（创建表、添加缺失列、种子数据）"""
        init_db()

    return app


# 创建应用实例（uvicorn 启动时会引用这个变量）
app = create_app()
