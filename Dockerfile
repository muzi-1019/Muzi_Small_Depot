# ============================
# RAG Role-Play System 后端 Dockerfile
# 多阶段构建：前端构建 + Python 后端
# ============================

# ---------- 阶段1：构建前端静态文件 ----------
FROM node:18-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY frontend/ ./
RUN npm run build

# ---------- 阶段2：Python 后端 ----------
FROM python:3.12-slim

# 系统依赖（PyMuPDF 需要部分 C 库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装 Python 依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制后端代码
COPY app/ ./app/
COPY main.py .
COPY scripts/ ./scripts/
COPY data/ ./data/

# 复制前端构建产物到 static 目录
COPY --from=frontend-builder /build/dist ./frontend/dist

# 创建必要目录
RUN mkdir -p uploads data/graphs log

# 环境变量默认值
ENV RAG_APP_ENV=prod \
    RAG_APP_DEBUG=false \
    RAG_UPLOAD_DIR=uploads

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
