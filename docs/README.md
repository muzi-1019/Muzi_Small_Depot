# RAG Role-Play System

基于 FastAPI 的「RAG + 多角色扮演」对话后端：MySQL 持久化会话、Redis 短期记忆、可选 Milvus 检索、可选 OpenAI 兼容大模型；提供知识库上传与元数据登记。

## 快速开始

1. `python -m venv .venv` 并激活虚拟环境。
2. `pip install -r requirements.txt`
3. 复制 `.env.example` 为 `.env`，配置 `RAG_MYSQL_DSN`、`RAG_REDIS_URL` 等。
4. 启动 MySQL / Redis（Milvus 可选）。
5. `python main.py`
6. 打开 `http://127.0.0.1:8000/docs` 查看 OpenAPI。

### 前端页面（React）

1. 进入 `frontend` 目录
2. 安装依赖：`npm install`
3. 启动页面：`npm run dev`
4. 访问：`http://127.0.0.1:5173`

前端已封装单页操作：注册/登录、角色选择、聊天、历史查看、知识文件上传（txt/pdf/md）。

> 若数据库已存在旧表结构，请手工执行迁移（新增列/新表），或重建开发库后让 SQLAlchemy `create_all` 自动建表。

## 主要 HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录（返回 JWT 与 `user_id`） |
| PATCH | `/api/auth/preferences` | 角色偏好 |
| GET | `/api/characters` | 角色列表 |
| POST | `/api/chat` 或 `/api/chat/send` | 发送消息 |
| GET | `/api/chat/history` | 对话历史 |
| POST | `/api/knowledge/upload` | 上传 txt/pdf/md |
| GET | `/api/knowledge/list` | 知识文件列表 |

更完整的字段级说明见 `docs/API接口文档.md`。

## 文档与测试资产

- `docs/需求规格说明书.md`
- `docs/系统设计文档.md`
- `docs/RAGAS评测说明.md`
- `postman/RAG-RolePlay.postman_collection.json`
- `jmeter/chat-stress-test.jmx`
- `tests/test_security.py`（`pytest`）

## 记忆与并发规则

- Redis 会话列表键：`chat:session:{user_id}:{character_id}`，默认保留最近 `RAG_SHORT_MEMORY_ROUNDS` 轮。
- 并发角色控制：`chat:active_roles:{user_id}`，默认最多 `RAG_MAX_CONCURRENT_ROLES_PER_USER=3`，空闲 `RAG_ACTIVE_ROLE_IDLE_SECONDS` 秒后释放名额。

## 配置提示

- `RAG_LLM_PROVIDER=mock`：本地模板回复。
- `RAG_LLM_PROVIDER=openai`（或 `vllm` / `sglang`）且配置 `RAG_OPENAI_API_BASE` / `RAG_OPENAI_API_KEY`：走 OpenAI 兼容 Chat Completions。
- `RAG_MILVUS_ENABLED=true`：尝试连接 Milvus；需 Collection 至少包含 `character_id` 与 `text` 字段，与示例查询一致。

## 密码哈希

默认使用 `pbkdf2_sha256`（`passlib`），避免部分环境下 `bcrypt` 与 `passlib` 版本不兼容问题。

## 项目结构

- `app/api`：路由
- `app/schemas`：请求/响应模型
- `app/repositories`：数据访问
- `app/services`：业务编排与外部系统边界
- `app/core`：配置、依赖、安全工具
- `scripts/export_ragas_rows.py`：导出 JSONL 供 RAGAS 使用
