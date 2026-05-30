# 项目模块职责说明

> 本文档按层次逐一说明每个模块/文件在项目中的具体作用、输入、输出和协作关系。

---

## 一、前端层 (`frontend/`)

### `frontend/src/App.jsx`
- **作用**：整个前端应用的主入口，承载所有 UI 状态和交互逻辑。
- **核心职责**：
  - 管理用户登录状态（JWT Token 存储与校验）
  - 角色选择界面（调用 `/characters` 接口）
  - 对话界面：消息列表渲染、SSE 流式接收、打字机动画、思考时间显示
  - 知识库上传页面（调用 `/knowledge` 接口）
  - 历史记录加载、搜索、导出
  - 等待提示控制：2-3 秒后逐字显示"正在进行检索，请稍等......"
- **输入**：用户点击、键盘输入、SSE `chunk` / `final_answer` 事件
- **输出**：HTTP 请求、SSE EventSource 连接、DOM 渲染

---

## 二、API 路由层 (`app/api/v1/`)

### `app/api/v1/auth_router.py`
- **作用**：处理用户认证相关请求
- **核心职责**：
  - `POST /auth/register`：接收用户名/密码，校验唯一性，密码哈希后存入 MySQL
  - `POST /auth/login`：校验密码，签发 JWT Token
  - `POST /auth/refresh`：刷新即将过期的 Token
- **输入**：前端提交的表单数据
- **输出**：JWT Token 字符串或 401 错误

### `app/api/v1/chat_router.py`
- **作用**：对话接口入口
- **核心职责**：
  - `POST /chat/stream`：接收用户问题和角色 ID，创建 SSE 响应，调用 `ChatService.send_message_stream()`
  - `POST /chat`：非流式对话，调用 `ChatService.send_message()`
  - 处理图片上传（base64 编码传给后端）
- **输入**：用户问题、角色 ID、图片数据（可选）
- **输出**：SSE 流（`chunk`、`rag_used`、`final_answer`、`sources`、`[DONE]`）或 JSON 响应

### `app/api/v1/character_router.py`
- **作用**：角色管理 CRUD
- **核心职责**：
  - `GET /characters`：返回所有可用角色列表
  - `GET /characters/{id}`：返回单个角色详情（人设、领域、提示词模板）
  - `POST /characters`（管理员）：创建新角色
  - `PUT /characters/{id}`（管理员）：修改角色信息
- **输入**：角色 ID、角色元数据
- **输出**：角色 JSON 对象

### `app/api/v1/knowledge_router.py`
- **作用**：知识库文档管理
- **核心职责**：
  - `POST /knowledge/upload`：接收 PDF 文件，调用 `PDFIngestService.ingest()` 解析入库
  - `GET /knowledge`：列出某角色下已上传的文档
  - `DELETE /knowledge/{doc_id}`：删除文档及关联向量
- **输入**：Multipart 文件上传、角色 ID
- **输出**：文档元数据、解析进度、索引状态

### `app/api/v1/graph_router.py`
- **作用**：知识图谱可视化查询
- **核心职责**：
  - `GET /graph/entities`：返回某角色知识库中的实体列表
  - `GET /graph/relations`：返回实体之间的关系
  - `POST /graph/build`：触发手动构建/重建图谱
- **输入**：角色 ID、查询参数
- **输出**：图谱节点和边的 JSON 数据

### `app/api/v1/admin_router.py`
- **作用**：管理后台接口
- **核心职责**：
  - `GET /admin/stats`：返回系统统计（用户数、对话数、文档数）
  - `GET /admin/users`：用户列表（分页）
  - `GET /admin/conversations`：会话监控
- **输入**：管理员 JWT Token（需鉴权）
- **输出**：统计数据 JSON

---

## 三、基础设施层 (`app/core/`)

### `app/core/config.py`
- **作用**：全局配置中心，所有参数的"唯一真相源"
- **核心职责**：
  - 从 `.env` 文件读取环境变量并类型校验
  - 定义所有默认值（数据库地址、API 密钥、模型名称、检索参数）
  - 被所有其他模块通过 `from app.core.config import settings` 引用
- **关键配置项**：
  - `RETRIEVAL_TOP_K=8`：混合检索召回 8 条候选
  - `HYBRID_VECTOR_WEIGHT=0.6` / `HYBRID_KEYWORD_WEIGHT=0.4`：语义与关键词权重
  - `RERANK_MODEL="BAAI/bge-reranker-v2-m3"`：精排模型
  - `LLM_MODEL_NAME="deepseek-ai/DeepSeek-V3"`：主大模型
  - `EMBEDDING_MODEL_NAME="BAAI/bge-large-zh-v1.5"`：Embedding 模型
  - `MILVUS_DIM=1024`：向量维度（必须与 Embedding 模型输出一致）
  - `NEO4J_ENABLED=True`：是否启用知识图谱
- **输入**：`.env` 文件或环境变量
- **输出**：`Settings` 配置对象

### `app/core/security.py`
- **作用**：密码加密与 JWT 令牌管理
- **核心职责**：
  - `verify_password()`：校验用户输入的密码是否与数据库中的哈希匹配
  - `get_password_hash()`：注册时用 `bcrypt` 对密码加密
  - `create_access_token()`：用 `python-jose` 签发 JWT（含用户 ID 和过期时间）
  - `decode_token()`：解析 Token，提取用户 ID
- **输入**：明文密码、JWT 字符串
- **输出**：哈希密码、Token payload 或异常

### `app/core/deps.py`
- **作用**：FastAPI 依赖注入容器
- **核心职责**：
  - `get_current_user()`：从 HTTP Header 提取 `Authorization: Bearer <token>`，校验 JWT，查询数据库返回用户对象
  - `get_db()`：创建 SQLAlchemy 数据库会话，请求结束后自动关闭
  - `require_admin()`：校验当前用户是否为管理员
- **输入**：HTTP 请求对象
- **输出**：User 模型实例、DB Session、权限校验结果

### `app/core/logging.py`
- **作用**：统一日志格式和级别
- **核心职责**：
  - 配置 `logging` 模块，统一输出格式（时间、级别、模块名、消息）
  - 记录接口请求耗时、响应状态码、错误堆栈
  - 区分开发环境（DEBUG）和生产环境（INFO）日志级别
- **输入**：各模块的日志调用
- **输出**：控制台/文件日志

---

## 四、数据层 (`app/db/`、`app/repositories/`)

### `app/db/models.py`
- **作用**：SQLAlchemy ORM 模型定义，描述数据库表结构
- **核心职责**：
  - 定义 `User`、`Character`、`Conversation`、`ChatMessage`、`KnowledgeDocument` 等表
  - 建立外键关系和索引
  - `Character` 表包含 `knowledge_base_id`，关联知识库
- **输入**：无（静态定义）
- **输出**：Python 类，可被 CRUD 操作

### `app/db/session.py`
- **作用**：数据库会话工厂
- **核心职责**：
  - 用 `create_engine()` 创建 MySQL 连接引擎
  - 用 `sessionmaker()` 创建会话工厂
  - 提供 `get_db()` 生成器，供 FastAPI 依赖注入使用

### `app/repositories/user_repository.py`
- **作用**：用户数据的增删改查封装
- **核心职责**：
  - `get_by_id()`：按 ID 查用户
  - `get_by_username()`：按用户名查用户（登录用）
  - `create()`：创建新用户
  - `update()`：更新用户信息
- **输入**：用户 ID、用户名、用户数据字典
- **输出**：User 模型实例或 None

### `app/repositories/character_repository.py`
- **作用**：角色数据的增删改查
- **核心职责**：
  - `list_all()`：返回所有角色
  - `get_by_id()`：按 ID 查角色
  - `create/update/delete()`：角色的 CRUD

### `app/repositories/conversation_repository.py`
- **作用**：会话和消息的管理
- **核心职责**：
  - `get_or_create()`：按 `(user_id, character_id)` 查找会话，不存在则创建
  - `add_message()`：向会话中插入一条消息
  - `get_messages()`：按会话 ID 分页查询历史消息
  - `update_timestamp()`：每次新消息后刷新会话更新时间

---

## 五、核心业务层 (`app/services/`)

### `app/services/chat_service.py` ⭐ 核心调度器
- **作用**：整个 RAG 对话流程的总指挥
- **核心职责**：
  1. **参数校验**：检查用户是否存在、角色是否有效
  2. **槽位管理**：调用 `MemoryService.ensure_concurrent_role_slot()` 确保用户同时只能与有限角色对话
  3. **Query Rewriting**：对多轮对话中的指代词（"他"、"这家公司"）进行消解
  4. **图像理解**：如有图片上传，调用 `ImageUnderstandingService.analyze()` 获取 OCR 文本和视觉描述
  5. **检索阶段**：
     - 调用 `PDFIngestService.search_with_meta()` 做混合检索（向量 + BM25）
     - 调用 `KnowledgeGraphService.graph_context()` 做图谱召回
  6. **精排**：对召回结果做 `bge-reranker-v2-m3` 重排序
  7. **上下文组装**：拼接检索到的 chunk 和图谱信息
  8. **记忆注入**：从 Redis 读取短期记忆，插入 Prompt
  9. **LLM 调用**：调用 `LLMService.generate_stream()` 流式生成
  10. **SSE 推送**：逐 chunk 推送给前端，最后推 `final_answer` 和 `sources`
  11. **持久化**：保存用户问题和 AI 回答到 MySQL，更新 Redis 记忆
- **输入**：`ChatRequest`（用户问题、角色 ID、图片、会话 ID）
- **输出**：SSE Generator / `ChatResponse`

### `app/services/llm_service.py`
- **作用**：封装所有大模型 API 调用
- **核心职责**：
  - `generate()`：非流式调用，等待完整回答后返回（用于摘要、实体提取、评估）
  - `generate_stream()`：流式调用，逐 token 返回 Generator（用于前端 SSE）
  - 构造符合 OpenAI 格式的请求体：`model`、`messages`、`temperature=0.7`、`max_tokens`
  - 支持多模型切换：DeepSeek-V3（主模型）、Qwen-VL（视觉模型）
  - 错误处理：API 超时、限流、空返回时的降级策略
- **输入**：角色人设、检索上下文、用户问题、对话历史
- **输出**：字符串（非流式）或 Generator（流式）

### `app/services/pdf_ingest_service.py` ⭐ RAG 引擎核心
- **作用**：PDF 解析、向量化、混合检索、精排，整个知识库的生命周期管理
- **核心职责**：
  - **解析阶段**：
    - 用 `PyMuPDF`（`fitz`）提取 PDF 文本、表格
    - 用 `rapidocr-onnxruntime` 对扫描页做 OCR
    - 提取图片，用 `Qwen/Qwen3-VL-8B` 生成图片描述文本
    - 最终把 PDF 变成"纯文本 + 表格文本 + 图片描述"的集合
  - **分块阶段**：
    - 按语义/长度切分成 chunk（约 200-500 字）
    - 保留 chunk 间的上下文重叠（overlap）
    - 记录来源：文件名、页码、chunk 序号
  - **向量化阶段**：
    - 调用 `BAAI/bge-large-zh-v1.5` API，每个 chunk 生成 1024 维向量
    - 写入 `Milvus`，Collection 名为 `character_knowledge_{character_id}`
  - **检索阶段**：
    - 把用户问题 Embedding
    - **向量检索**：Milvus COSINE 相似度，召回 Top-K
    - **关键词检索（BM25）**：`jieba` 分词后做 TF-IDF 匹配，召回 Top-K
    - **分数融合**：`0.6 * 向量分数 + 0.4 * 关键词分数`
    - **精排（Rerank）**：调用 `BAAI/bge-reranker-v2-m3` API，判断"问题-片段"真实相关性，取前 5 个
  - **辅助方法**：
    - `has_data()`：检查某角色是否有知识库数据
    - `delete_by_doc_id()`：删除文档及关联向量
- **输入**：PDF 文件（字节流）、用户问题（字符串）
- **输出**：解析后的 chunk 列表（入库时）或检索结果列表（查询时）

### `app/services/graph_service.py`
- **作用**：知识图谱的构建与查询
- **核心职责**：
  - **构建阶段**（PDF 入库时触发）：
    - 调用 LLM 从 chunk 中提取实体（如"程家明"、"武汉兴图"）
    - 提取关系（如"控股股东"、"注册地"）
    - 用 `py2neo` 写入 Neo4j：`MERGE (a:Entity {name:...})-[r:REL {type:...}]->(b:Entity)`
  - **查询阶段**（用户提问时）：
    - 用 LLM 从问题中提取关键实体
    - Cypher 查询：`MATCH (e:Entity)-[r]-(related) WHERE e.name IN [...] RETURN ...`
    - 遍历 1-2 跳关系，收集相关三元组
    - 把三元组拼成自然语言文本，追加到检索上下文
  - `has_graph()`：检查某角色是否已构建图谱
- **输入**：chunk 文本（构建时）或用户问题（查询时）
- **输出**：图谱三元组文本 或 空字符串（无图谱时）

### `app/services/image_understanding_service.py`
- **作用**：用户上传图片的多模态理解
- **核心职责**：
  - `_decode_image()`：解析 base64 编码的图片数据，支持 PNG/JPG/JPEG
  - `_ocr_image()`：用 RapidOCR 识别图片中的文字
  - `_describe_image()`：调用 `Qwen/Qwen3-VL-8B` API，生成图片内容的中文描述
  - `analyze()`：整合 OCR 文本 + 视觉描述，返回多模态上下文
- **输入**：base64 编码的图片字符串 + MIME 类型
- **输出**："OCR 文本 + 图片描述"的拼接字符串

### `app/services/memory_service.py`
- **作用**：管理对话的短期记忆
- **核心职责**：
  - **存储**：以 Redis Hash / List 结构存储，key 为 `memory:{user_id}:{character_id}`
  - **读取**：返回最近 `short_memory_rounds`（默认 20 轮）的对话历史
  - **自动摘要**：当轮数超过 `auto_summary_threshold`（默认 10 轮），调用 LLM 生成摘要，替换原始对话，压缩长度
  - **槽位管理**：`ensure_concurrent_role_slot()` 限制用户同时活跃的角色数
- **输入**：用户 ID、角色 ID、新消息
- **输出**：对话历史字符串（用于 Prompt 拼接）

### `app/services/context_service.py`
- **作用**：注入实时外部信息
- **核心职责**：
  - 获取当前时间、天气等实时数据
  - 拼接成简短文本，插入到 Prompt 中
  - 让角色回答具有时效性（比如"今天"指的是真实今天）
- **输入**：无（自动获取）
- **输出**：实时信息字符串（如"当前时间：2026-05-30 10:00"）

---

## 六、评估与脚本层 (`scripts/`)

### `scripts/eval_comprehensive.py`
- **作用**：系统功能测试与评估（Task 07 + Task 09）
- **核心职责**：
  - 定义 10 个测试问题（来自 sample_questions.pdf），含 ground truth 关键词
  - 分别运行三种模式：`run_rag()`、`run_graph_rag()`、`run_pure_llm()`
  - 对每个回答计算 5 项指标：
    - `keyword_accuracy`：关键词命中率（字符串匹配）
    - `context_precision`：LLM-as-Judge 评估检索片段相关性
    - `context_recall`：LLM-as-Judge 评估回答信息来源覆盖率
    - `faithfulness`：LLM-as-Judge 评估回答是否忠实于上下文
    - `answer_relevancy`：LLM-as-Judge 评估回答是否切题
  - `llm_judge()`：调用 DeepSeek-V3 做自动评判
  - `generate_report()`：汇总为 Markdown 报告 `eval_comprehensive_report.md`
- **输入**：预定义的测试问题集
- **输出**：`eval_comprehensive_report.md`（含逐题对比和总览数据）

### `scripts/build_graph.py`
- **作用**：手动触发知识图谱构建
- **核心职责**：
  - 读取某角色的所有知识库 chunk
  - 逐条调用 LLM 提取实体和关系
  - 写入 Neo4j

### `scripts/benchmark_rag.py`
- **作用**：性能压测脚本
- **核心职责**：
  - 模拟并发用户发送请求
  - 记录响应时间、吞吐量、错误率
  - 生成性能报告

---

## 七、配置与部署文件

### `.env.example`
- **作用**：环境变量模板，列出所有可配置项和默认值
- **核心职责**：
  - 新部署时复制为 `.env`，填入真实的数据库地址、API 密钥
  - 避免敏感信息硬编码到代码中

### `requirements.txt`
- **作用**：Python 依赖清单
- **核心依赖**：
  - `fastapi` + `uvicorn`：Web 框架
  - `sqlalchemy` + `PyMySQL`：ORM 和数据库驱动
  - `redis`：缓存客户端
  - `pymilvus`：向量库客户端
  - `pypdf` + `PyMuPDF`：PDF 解析
  - `rapidocr-onnxruntime`：OCR 引擎
  - `httpx`：HTTP 客户端（调用 SiliconFlow API）
  - `python-jose` + `passlib`：JWT 和密码加密
  - `jieba`：中文分词（BM25 检索用）
  - `pytest`：测试框架

### `docker-compose.yml`
- **作用**：一键编排所有服务
- **核心职责**：
  - 定义后端、前端（Nginx）、MySQL、Redis、Milvus 容器
  - 配置端口映射、卷挂载、环境变量注入
  - 实现"一条命令启动整个系统"

### `Dockerfile`
- **作用**：后端服务的容器镜像构建脚本
- **核心职责**：
  - 基于 Python 3.10 镜像
  - 安装 `requirements.txt`
  - 复制项目代码
  - 暴露 8000 端口，启动 Uvicorn

### `nginx.conf`
- **作用**：生产环境 Nginx 反向代理配置
- **核心职责**：
  - `/` → 前端静态资源（`frontend/dist/`）
  - `/api` → 后端 FastAPI（`localhost:8000`）
  - SSE 长连接：`proxy_buffering off; proxy_cache off;`
  - Gzip 压缩、静态文件缓存

---

## 八、测试层 (`tests/`)

### `tests/test_auth.py`
- **作用**：用户认证接口的单元测试
- **核心职责**：
  - 测试注册、登录、Token 刷新
  - 测试密码校验、过期 Token 处理

### `tests/test_chat.py`
- **作用**：对话接口的集成测试
- **核心职责**：
  - 测试流式 SSE 响应格式
  - 测试 RAG 检索是否返回 sources
  - 测试图片上传后的多模态响应

---

## 模块协作关系图（简化版）

```
用户请求
  ↓
[API 路由] → [deps.py 鉴权] → [ChatService]
                              ↓
          ┌───────────────────┼───────────────────┐
          ↓                   ↓                   ↓
    [MemoryService]    [PDFIngestService]   [GraphService]
          ↓                   ↓                   ↓
       (Redis)          (Milvus + BM25)      (Neo4j)
                              ↓
                        [Rerank API]
                              ↓
                        [LLMService]
                              ↓
                         (SiliconFlow)
                              ↓
                         [SSE 返回前端]
```

---













## 二、聊天请求处理流程

这是用户提问后，后端处理一条消息的**完整流水线**，共 10 步：

**① JWT 鉴权**

- 从请求 Header 提取 `Authorization: Bearer <token>`
- `security.py` 解析 JWT，校验签名和过期时间
- `deps.py` 从数据库加载用户对象，注入到路由

**② 屏蔽词检测**

- 检查用户问题中是否包含敏感词/违禁词
- 如果命中，直接返回拒绝回答，不走后续流程

**③ 加载记忆（Redis 20 轮）**

- `MemoryService` 从 Redis 读取该用户和该角色的最近对话
- `short_memory_rounds=20`，默认保留 20 轮（一问一答算一轮）
- 超过 10 轮会自动调用 LLM 生成摘要，压缩记忆长度

**④ Query Rewrite（LLM 改写）**

- 多轮对话中用户常使用指代词："他"、"这家公司"、"上面提到的"
- `query_rewrite_enabled=True` 时，ChatService 调用 LLM 把问题改写成**自包含形式**
- 例："他住在哪里？" → "程家明住在哪里？"

**⑤ 混合检索（核心 RAG）**





```
BM25 关键词检索          ANN 向量检索
    ↓                       ↓
  jieba 分词            bge-large-zh Embedding
  权重 0.4               权重 0.6
    ↘                   ↙
      融合去重（分数加权）
              ↓
        Rerank 精排
      bge-reranker-v2-m3
              ↓
        取 Top 5 片段
```

- **BM25**：适合精确匹配（人名、地名、金额、股票代码）
- **ANN 向量**：适合语义泛化（同义词、改写句）
- **融合去重**：同一文档被两路都召回时，取更高分的那份
- **Rerank**：用 `bge-reranker-v2-m3` 判断"问题-片段"的真实相关性，比单纯分数更准确

**⑥ 图谱增强（Neo4j / LightRAG）**

- `GraphService.graph_context()` 从问题中提取实体
- 在 Neo4j 中查关系：`MATCH (e)-[r]-(related) WHERE e.name IN [...]`
- LightRAG 同时做"向量 + 图谱"混合召回
- 把找到的三元组（如"程家明 - 控股股东 - 武汉兴图"）拼成文本
- **追加到检索上下文**，让 LLM 能看到结构化关系

**⑦ 构建 Prompt**

- 把以下部分按顺序拼接：

  

  

  ```
  [角色人设：你是招股说明书分析师...]
  [检索上下文：5 个 chunk + 图谱三元组]
  [对话记忆：最近 20 轮]
  [实时信息：当前时间（ContextService）]
  [用户问题]
  ```

- 最终形成一个完整的 System + User Prompt

**⑧ LLM 流式生成（DeepSeek-V3）**

- `LLMService.generate_stream()` 调用 SiliconFlow API
- `stream=true`，模型逐个 token 返回
- 后端用 `yield` 生成 SSE 事件流

**⑨ SSE 推送前端**

- 后端格式：`data: {"chunk": "今"}\n\n`
- 前端 `EventSource` 接收，逐字追加到消息文本
- 同时推送 `rag_used`（是否用了检索）、`sources`（引用来源）
- 最后发送 `data: [DONE]\n\n` 标记结束

**⑩ 持久化（MySQL + Redis）**

- **MySQL**：保存 `ChatMessage` 记录（user 消息 + assistant 消息）
- **Redis**：更新该用户的对话记忆列表
- `Conversation.updated_at` 刷新时间戳

------

## 三、知识入库流程

这是**PDF 文档如何变成可检索知识**的全过程：

**① PDF 文件上传**

- 前端通过 `/knowledge/upload` 上传
- 后端接收 `multipart/form-data`，保存为临时文件

**② PyPDF2 解析**

- 用 `PyMuPDF`（`fitz`）逐页提取文本
- 同时提取：
  - 表格结构（如果有的话）
  - 页面中的图片
  - OCR 识别扫描页文字

**③ 固定分块（800 字 / 120 重叠）**

- 把长文本切成约 **800 字**的 chunk
- 相邻 chunk 之间有 **120 字重叠**，保证上下文连续性
- 每个 chunk 记录来源：文件名、页码、chunk 序号

**④ 向量化 + BM25 索引（并行）**

- **Embedding 向量化**：
  - 调用 `BAAI/bge-large-zh-v1.5` API
  - 每个 chunk 生成 **1024 维**密集向量
- **jieba 分词 + BM25 索引**：
  - `jieba.cut()` 对 chunk 做中文分词
  - 计算每个词的 TF-IDF 分数，建立稀疏索引

**⑤ 写入 Milvus**

- Collection 名：`character_knowledge_{character_id}`
- 每个角色独立一个 Collection，知识互不干扰
- 向量相似度度量：**COSINE**（余弦相似度）
- 存储字段：向量、原始文本、来源文件、页码、BM25 分数

------

## 四、服务依赖关系

这张图展示了 `ChatService` 如何**编排**各个子服务：





```
ChatService（聊天编排中心）
    │
    ├──→ MemoryService（记忆）
    │       └── 从 Redis 读取/写入对话历史
    │
    ├──→ PDFIngestService（检索）
    │       └── 混合检索 → Rerank → 返回 Top 5 片段
    │
    ├──→ LightRAGService（图+向量检索）
    │       └── 向量召回 + 图谱关系召回 → 融合
    │
    ├──→ ContextService（环境）
    │       └── 获取当前时间、天气等实时信息
    │
    └──→ GraphService（图谱）
            └── Neo4j 实体关系查询
    │
    └──→ LLMService（大模型）
            ← 接收以上所有服务的输出
            ← 组装 Prompt → 调用 DeepSeek-V3
            → 生成回答
```

**依赖关系解读**：

- `ChatService` 是**总指挥**，不负责具体检索或生成，只负责**调度**

- `MemoryService`、

  PDFIngestService

  、

  ```
  LightRAGService
  ```

  、

  ```
  ContextService
  ```

  、

  ```
  GraphService
  ```

   是

  并行/半并行

  调用的（部分

  可并发

  ，部分有先后

  依赖

  ）

  

- 所有子服务的结果最终汇聚到 `LLMService`

- `LLMService` 是唯一调用外部 SiliconFlow API 的入口，统一封装

------

## 三张图之间的关系

| 图               | 阶段     | 说明                                      |
| :--------------- | :------- | :---------------------------------------- |
| **聊天请求处理** | 运行时   | 用户问一句话，后端怎么一步步生成回答      |
| **知识入库流程** | 离线时   | PDF 怎么被解析、分块、向量化、存入 Milvus |
| **服务依赖关系** | 架构层面 | 各个 Service 之间谁调用谁，数据怎么汇聚   |

**一句话总结**：

- **入库**：PDF → 解析 → 分块 → Embedding → Milvus（提前准备好知识）
- **对话**：鉴权 → 检索 → 精排 → 图谱 → 组装 Prompt → LLM 生成 → SSE 推送（实时回答问题）
- **依赖**：ChatService orchestrates（编排）所有子服务，最终汇聚到 LLMService





























## 第一层：前端（浏览器 + React + Vite）

**前端 `:5173`**（开发环境），生产环境通过 Nginx 代理到 `:80`

| 模块         | 图中文字 | 作用                                        |
| :----------- | :------- | :------------------------------------------ |
| 登录注册     | —        | 收集账号密码，获取 JWT Token                |
| 角色选择     | —        | 从 `/characters` 拉取角色列表，进入专属对话 |
| 聊天界面 SSE | —        | `EventSource` 连接后端，逐字渲染回答        |
| 知识库管理   | —        | 上传 PDF，查看入库状态                      |

**连接方式**：`HTTP/SSE` 连接到后端 `FastAPI :8000`

------

## 第二层：API 路由层（FastAPI `:8000`）

所有接口统一由 FastAPI 暴露：

| 路由          | 图中文字   | 功能                     |
| :------------ | :--------- | :----------------------- |
| `/auth`       | JWT 鉴权   | 登录、注册、Token 刷新   |
| `/chat`       | 流式聊天   | SSE 长连接，实时推送回答 |
| `/characters` | 角色 CRUD  | 查询/创建角色            |
| `/knowledge`  | 知识库管理 | PDF 上传、文档列表、删除 |
| `/graph`      | 图谱查询   | 知识图谱可视化           |
| `/admin`      | 后台       | 统计面板、用户管理       |

**每一个路由的通用流程**：





```
前端请求 → API 路由 → deps.py 鉴权 → 调用 Service → 返回 JSON/SSE
```

------

## 第三层：基础设施（支撑层）

图中灰色框标注的四个模块，为所有业务提供**公共服务**：

| 模块          | 图中文字 | 作用                                                 |
| :------------ | :------- | :--------------------------------------------------- |
| config.py     | 统一配置 | 从 .env 读取数据库地址、API 密钥、模型名称、检索参数 |
| `security.py` | 密码+JWT | bcrypt 加密密码、python-jose 签发/校验 Token         |
| `deps.py`     | 依赖注入 | `get_current_user()` 从 Header 提取并校验 JWT        |
| `logging.py`  | 统一日志 | 记录请求耗时、接口路径、错误堆栈                     |

**关键**：这四个模块**不处理业务逻辑**，只提供**工具能力**，被所有路由和服务共享。

------

## 第四层：核心服务层（ChatService 编排中心）

**`ChatService`**（聊天编排中心）是**总指挥**，本身不做具体检索或生成，只负责**调度**。

图中显示 ChatService 连接了 6 个子服务，箭头方向表示**调用关系**：





```
ChatService
    ├──→ ContextService   (天气/时间)
    ├──→ LightRAGService (图+向量混合)
    ├──→ MemoryService    (短期记忆)
    ├──→ GraphService     (知识图谱)
    ├──→ PDFIngestService (PDF解析+混合检索)
    └──→ LLMService       (大模型调用)
```

**每个子服务的职责**：

| 子服务            | 图中文字         | 具体做什么                            | 输出给 ChatService                         |
| :---------------- | :--------------- | :------------------------------------ | :----------------------------------------- |
| `ContextService`  | 天气/时间        | 获取当前时间、天气等实时信息          | `"当前时间：2026-05-30 10:14"`             |
| `LightRAGService` | 图+向量混合      | 向量召回 + 图谱关系召回，两路融合     | 相关 chunk + 关系文本                      |
| `MemoryService`   | 短期记忆         | 从 Redis 读取最近 20 轮对话           | 对话历史字符串                             |
| `GraphService`    | 知识图谱         | Neo4j 实体关系遍历查询                | 三元组文本（如"程家明-控股股东-武汉兴图"） |
| PDFIngestService  | PDF解析+混合检索 | BM25 + 向量检索 → Rerank 精排         | Top 5 最相关 chunk                         |
| `LLMService`      | 大模型调用       | 封装 SiliconFlow API，流式/非流式生成 | AI 回答文本                                |

**ChatService 的工作方式**：

1. 接收用户问题
2. **并行/串行**调用 6 个子服务收集信息
3. 把 6 路结果**汇聚**成一个完整 Prompt
4. 交给 LLMService 生成回答
5. SSE 推送给前端

------

## 第五层：数据存储（底层）

所有子服务最终落到这里：

| 存储                | 图中文字            | 存什么                                                       | 哪个服务在用                  |
| :------------------ | :------------------ | :----------------------------------------------------------- | :---------------------------- |
| **MySQL**           | 用户/角色/会话/消息 | 用户表、角色表、会话表、聊天消息、文档元数据                 | 所有需要持久化的数据          |
| **Redis**           | 短期记忆/缓存       | 最近 20 轮对话、Token 黑名单、临时缓存                       | MemoryService                 |
| **Neo4j**           | 知识图谱            | 实体节点（如"程家明"）和关系边（如"控股股东"）               | GraphService、LightRAGService |
| **Milvus**          | 向量+BM25           | 1024 维向量 + 原始文本 + BM25 索引                           | PDFIngestService              |
| **SiliconFlow API** | LLM+Embed+Rerank    | DeepSeek-V3（生成）、bge-large-zh（Embedding）、bge-reranker-v2-m3（精排） | LLMService、PDFIngestService  |

**注意**：SiliconFlow API 不是本地存储，是云端服务，但图中把它和数据层放在一起，因为它是整个系统的"外部知识/计算源"。

------

## 数据流动全景（以一次对话为例）





```
[用户输入] "公司的控股股东是谁？"
    ↓ HTTP POST
[前端 :5173] → [FastAPI :8000 /chat]
    ↓
[deps.py] 校验 JWT → 获取 user_id
    ↓
[ChatService] 开始编排：
    ├── 调用 MemoryService → [Redis] 读取历史对话
    ├── 调用 PDFIngestService → [Milvus] 向量+BM25 检索 + [SiliconFlow] Rerank → 返回 Top 5 chunk
    ├── 调用 GraphService → [Neo4j] 查询"控股股东"关系 → 返回三元组
    ├── 调用 LightRAGService → [Milvus+Neo4j] 图向量混合召回
    └── 调用 ContextService → 获取当前时间
    ↓
[汇总所有信息] 构建完整 Prompt
    ↓
[LLMService] → [SiliconFlow API] 调用 DeepSeek-V3 流式生成
    ↓ SSE
[前端] 逐字渲染 "公司的控股股东是程家明[3]..."
    ↓
[持久化] MySQL 保存消息 + Redis 更新记忆
```

------

## 图中关键设计思想

1. **ChatService 是编排中心，不是执行中心** — 具体检索、记忆、图谱、生成都交给子服务，ChatService 只负责调度
2. **PDFIngestService 是 RAG 引擎核心** — 它同时负责"入库"（PDF → chunk → Embedding → Milvus）和"检索"（用户问题 → 混合检索 → Rerank）
3. **数据存储各司其职** — 关系数据 MySQL、向量检索 Milvus、图关系 Neo4j、临时记忆 Redis、生成能力 SiliconFlow
4. **每角色独立知识空间** — Milvus 的 Collection 是 `character_knowledge_{id}`，Neo4j 也有角色隔离

这就是你架构图里每一层、每一块的具体含义和协作关系。



Feedback submitted
