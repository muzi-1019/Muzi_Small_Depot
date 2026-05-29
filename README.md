# 基于 RAG 的角色扮演对话系统

> **Role-playing system based on RAG** — 一个支持多角色、多用户、知识库动态更新的智能问答系统，融合传统 RAG、Graph+RAG、混合检索、精排与多模态图像理解。

---

## 目录

- [功能特性](#功能特性)
- [技术架构](#技术架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [核心流程](#核心流程)
- [评估与优化](#评估与优化)
- [部署方式](#部署方式)
- [技术亮点](#技术亮点)

---

## 功能特性

- **多角色扮演**：支持医生、律师、金融分析师、虚拟朋友等多种角色，每个角色拥有独立人设、提示词模板与知识库。
- **混合检索**：向量检索（Milvus）+ 关键词检索（BM25）+ 知识图谱召回（Neo4j），三路互补，兼顾语义泛化与精确匹配。
- **Graph+RAG**：基于 Neo4j 构建实体-关系知识图谱，支持多跳推理，提升结构化信息（如股权关系、供应链）的问答准确率。
- **精排优化**：使用 `BAAI/bge-reranker-v2-m3`（SiliconFlow API）对召回结果重排序，提升上下文精度。
- **多模态理解**：支持用户上传图片（OCR + 视觉模型解析）和 PDF 文档（文本提取 + 表格解析 + 图像描述）。
- **流式对话**：后端 SSE 实时推送，前端逐字打字机效果，降低用户等待焦虑。
- **对话记忆**：短期记忆（最近 N 轮）+ 自动摘要，支持多轮对话与指代消解（Query Rewriting）。
- **完整评估**：内置 `eval_comprehensive.py`，支持 RAG / Graph+RAG / Pure LLM 三模式对比评估，覆盖上下文精度、召回、忠实度、相关性等指标。

---

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (React + Vite)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐│
│  │   角色选择    │  │   对话界面    │  │   知识库 / 历史管理  ││
│  └──────────────┘  └──────────────┘  └─────────────────────┘│
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / SSE
┌────────────────────────▼────────────────────────────────────┐
│                    FastAPI 后端服务                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ 用户认证  │ │ 角色管理  │ │ 对话管理  │ │ 知识库管理   │ │
│  │ (JWT)    │ │          │ │          │ │ (PDF/图片)  │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘ │
│                         │                                  │
│  ┌──────────────────────▼──────────────────────────────┐   │
│  │              Chat Service (RAG Pipeline)           │   │
│  │  1. Query Rewriting → 2. Hybrid Retrieval          │   │
│  │  3. Rerank (bge-reranker-v2-m3) → 4. LLM Generate │   │
│  └────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   ┌─────────┐    ┌─────────┐     ┌──────────┐
   │  MySQL   │    │  Redis   │     │  Milvus   │
   │ 关系数据  │    │  缓存    │     │ 向量检索  │
   └─────────┘    └─────────┘     └──────────┘
        │                              │
        ▼                              ▼
   ┌─────────┐                   ┌──────────┐
   │  Neo4j  │                   │ LLM API  │
   │ 图谱召回 │                   │SiliconFlow│
   └─────────┘                   └──────────┘
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | React 18 + Vite |
| 关系数据库 | MySQL + SQLAlchemy + PyMySQL |
| 缓存 | Redis |
| 向量数据库 | Milvus (pymilvus) |
| 知识图谱 | Neo4j (py2neo / neo4j) |
| Embedding | BAAI/bge-large-zh-v1.5 (API) |
| Rerank | BAAI/bge-reranker-v2-m3 (SiliconFlow API) |
| 大模型 | SiliconFlow / OpenAI 兼容 API (DeepSeek-V3 等) |
| 视觉模型 | Qwen/Qwen3-VL-8B-Instruct |
| OCR | RapidOCR (ONNXRuntime) |
| PDF 解析 | PyMuPDF + PyPDF |
| 测试 | pytest + JMeter |
| 部署 | Docker + Docker Compose + Nginx |

---

## 项目结构

```
Role-playing system based on RAG_new/
├── app/                          # FastAPI 后端
│   ├── api/v1/                   # API 路由
│   ├── core/                     # 配置、常量、工具函数
│   ├── db/                       # 数据库模型与初始化
│   ├── repositories/             # 数据仓库层
│   ├── services/                 # 业务逻辑层
│   │   ├── chat_service.py       # 对话与 RAG 流程
│   │   ├── pdf_ingest_service.py # PDF 解析与向量化
│   │   ├── graph_service.py      # 知识图谱构建与检索
│   │   ├── llm_service.py        # 大模型调用封装
│   │   └── image_understanding_service.py # 图像 OCR + 视觉描述
│   └── main.py                   # 应用入口
├── frontend/                     # React 前端
│   ├── src/
│   │   └── App.jsx               # 主应用（角色、对话、流式渲染）
│   ├── index.html
│   └── package.json
├── data/                         # 数据集、知识库、评估样本
│   ├── finetune/                 # 微调数据（pairs/triplets）
│   ├── graphs/                   # 导出图谱 JSON
│   └── lightrag/                 # LightRAG 数据
├── docs/                         # 项目文档、工单、答辩材料
├── scripts/                      # 工具脚本
│   ├── eval_comprehensive.py     # 综合评估（Task 07 + Task 09）
│   ├── build_graph.py            # 知识图谱构建
│   ├── build_lightrag.py         # LightRAG 构建
│   └── benchmark_rag.py          # 性能压测
├── tests/                        # 单元 / 集成测试
├── jmeter/                       # JMeter 压测脚本
├── requirements.txt              # Python 依赖
├── Dockerfile                    # 后端镜像
├── docker-compose.yml            # 一键编排
├── nginx.conf                    # 生产环境 Nginx 配置
├── .env.example                  # 环境变量模板
└── README.md                     # 本文件
```

---

## 快速开始

### 1. 环境准备

确保已安装：
- Python 3.10+
- Node.js 18+
- MySQL 8.0
- Redis 6+
- Milvus 2.3+
- Neo4j 5.x (可选，Graph+RAG 需要)

### 2. 后端启动

```bash
# 克隆或进入项目目录
cd "Role-playing system based on RAG_new"

# 安装依赖
pip install -r requirements.txt

# 复制并编辑环境变量
cp .env.example .env
# 修改 .env 中的数据库地址、API 密钥等

# 初始化数据库
python -c "from app.db.init_db import init_db; init_db()"

# 启动服务
python app/main.py
# 或使用 uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 前端启动

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`，后端在 `http://localhost:8000`。

### 4. 一键 Docker 部署

```bash
docker-compose up -d
```

将启动：后端、前端（通过 Nginx 代理）、MySQL、Redis、Milvus。

---

## 配置说明

核心配置位于 `app/core/config.py`，通过 `.env` 文件覆盖：

| 变量 | 说明 | 示例 |
|------|------|------|
| `MYSQL_DSN` | MySQL 连接地址 | `mysql+pymysql://root:root@localhost:3306/rag_db` |
| `REDIS_URL` | Redis 地址 | `redis://localhost:6379/0` |
| `MILVUS_URL` | Milvus 地址 | `http://localhost:19530` |
| `NEO4J_URI` | Neo4j Bolt 地址 | `bolt://localhost:7687` |
| `OPENAI_API_BASE` / `OPENAI_API_KEY` | 大模型 API 地址和密钥 | `https://api.siliconflow.cn/v1` |
| `LLM_MODEL_NAME` | 大模型名称 | `deepseek-ai/DeepSeek-V3` |
| `EMBEDDING_MODEL_NAME` | Embedding 模型 | `BAAI/bge-large-zh-v1.5` |
| `RERANK_MODEL` | 精排模型 | `BAAI/bge-reranker-v2-m3` |
| `RETRIEVAL_TOP_K` | 检索候选数 | `8` |
| `RERANK_TOP_K` | 精排后保留数 | `5` |
| `HYBRID_VECTOR_WEIGHT` | 向量检索权重 | `0.6` |
| `HYBRID_KEYWORD_WEIGHT` | 关键词检索权重 | `0.4` |
| `NEO4J_ENABLED` | 是否启用图谱 | `true` |

完整配置模板见 `.env.example`。

---

## 核心流程

### RAG 问答流程

```
用户提问
  → Query Rewriting（多轮指代消解）
  → Hybrid Retrieval（向量 + 关键词 + 图谱）
  → Rerank（bge-reranker-v2-m3）
  → Prompt 构建（角色人设 + 检索上下文 + 记忆）
  → LLM 流式生成（SSE 推送）
  → 前端打字机渲染
  → 保存消息到 MySQL
```

### PDF 入库流程

```
上传 PDF
  → 文本提取（PyMuPDF）
  → 表格解析 + OCR 识别
  → 图像提取 → 视觉模型描述
  → 分块（chunk）+ Embedding
  → 写入 Milvus（向量库）
  → 构建 Neo4j 知识图谱（实体/关系提取）
```

---

## 评估与优化

### 评估脚本

```bash
python scripts/eval_comprehensive.py
```

生成 `eval_comprehensive_report.md`，对比三种模式：
- **RAG**：传统向量 + 关键词 + 精排
- **Graph+RAG**：在上述基础上叠加 Neo4j 图谱召回
- **Pure LLM**：无检索，纯大模型生成

评估指标：
- `keyword_accuracy`：关键词命中率
- `context_precision`：检索片段相关度（目标 ≥ 0.8）
- `context_recall`：回答信息来源覆盖率（目标 ≥ 0.9）
- `faithfulness`：忠实度（是否有幻觉）
- `answer_relevancy`：回答相关性
- `total_time`：端到端响应时间（目标 ≤ 3s）

### 当前基线与优化方向

| 指标 | 基线（实测） | 目标 | 优化方向 |
|------|-------------|------|----------|
| 上下文精度 | 42% | ≥ 80% | 收紧候选集、提高语义权重、优化精排 |
| 上下文召回 | 72% ~ 80% | ≥ 90% | 增大 chunk 重叠、加深图谱遍历、同义词扩展 |
| 响应时间 | 14.8s | ≤ 3s | 检索并行化、Embedding 缓存、轻量模型 |

详细优化方案见 `optimization_proposal.md`。

---

## 部署方式

### 本地开发

```bash
# 后端
python app/main.py

# 前端
cd frontend && npm run dev
```

### Docker 生产部署

```bash
# 构建并启动全部服务
docker-compose up -d --build

# 查看日志
docker-compose logs -f backend

# 停止
docker-compose down
```

### Nginx 反向代理

生产环境使用 `nginx.conf` 配置：
- 前端静态资源：`/` → `frontend/dist/`
- 后端 API：`/api` → `localhost:8000`
- SSE 长连接：关闭缓冲区，保障流式响应

---

## 技术亮点

1. **三路混合检索**：向量语义 + 关键词精确 + 图谱关系，覆盖不同查询场景。
2. **LLM-as-Judge 评估**：使用大模型自动评判检索质量和回答质量，减少人工标注成本。
3. **多模态文档理解**：PDF 中的文字、表格、图像均通过 OCR / 视觉模型转换为可检索文本。
4. **实时流式交互**：SSE 流式输出 + 前端打字机动画，支持思考时间提示与等待语句。
5. **角色化知识隔离**：每个角色拥有独立 Milvus Collection 和 Neo4j 子图，知识互不干扰。

---

## 许可证

本项目为教学与科研用途开发，未经授权不得用于商业目的。

---

> 如有问题，请参考 `docs/` 目录下的项目文档与工单说明，或查看 `eval_comprehensive_report.md` 了解当前系统评估详情。
