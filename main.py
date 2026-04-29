"""
本文件的作用：项目根目录的启动入口文件。
运行 python main.py 即可启动整个后端服务器。
使用 uvicorn 作为 ASGI 服务器来运行 FastAPI 应用。
"""

import sys                # 系统模块，用于修改 Python 模块搜索路径
from pathlib import Path  # 文件路径处理

import uvicorn  # ASGI 服务器（用于运行 FastAPI 应用）

ROOT = Path(__file__).resolve().parent  # 项目根目录的绝对路径
BACKEND_PORT = 8000                     # 后端服务器监听端口


if __name__ == "__main__":
    # 确保项目根目录在 Python 模块搜索路径中（这样才能正确导入 app 包）
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    print(f"Backend API starting at http://127.0.0.1:{BACKEND_PORT}")
    uvicorn.run(
        "app.main:app",                    # 指向 app/main.py 中的 app 变量
        host="0.0.0.0",                    # 监听所有网络接口
        port=BACKEND_PORT,                 # 监听端口
        reload=True,                       # 开启热重载（代码修改后自动重启）
        reload_dirs=[str(ROOT / "app")],   # 只监控 app 目录的文件变化
    )
