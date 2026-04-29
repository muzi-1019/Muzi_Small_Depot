# 更新公告

## v1.7.0（2026-04-28）

### ✨ 新功能

- **实时上下文感知**：对话时自动获取用户当前时间、所在地理位置（城市/地区/国家）和当地实时天气（温度/体感/湿度/天气状况），并注入大模型提示词中，让 AI 角色能感知用户的真实环境
- **新增 `ContextService`**：`app/services/context_service.py`，通过 ip-api.com（IP 地理定位）和 wttr.in（天气查询）两个免费 API 获取实时信息，无需额外 API Key
- **智能缓存**：同一 IP 的地理位置和天气数据缓存 10 分钟，避免频繁调用外部 API
- **提示词增强**：LLM 请求中新增【当前环境信息】区块，AI 可自然地结合时间/地点/天气进行对话

### 🔧 改动文件

- `app/services/context_service.py` — 新增实时上下文服务
- `app/api/v1/chat.py` — 聊天接口提取客户端 IP 并传递
- `app/services/chat_service.py` — 调用 ContextService 获取实时上下文
- `app/services/llm_service.py` — 提示词中加入【当前环境信息】区块

---

## v1.6.0（2026-04-24）

### ✨ 新功能

- **管理员后台仪表盘**：管理员在聊天页头部点击「仪表盘」按钮，弹出面板展示全局统计卡片（用户/会话/消息/角色/知识库数量）及用户列表、最近会话、知识库文件三张数据表
- **管理员 API**：`GET /api/admin/stats`、`/users`、`/conversations`、`/knowledge`，均需管理员权限
- **会话自动摘要**：对话轮数超过阈值（默认 10 轮）时，自动调用大模型生成前文摘要并缓存至 Redis，后续请求自动携带摘要压缩上下文窗口
- **消息搜索**：聊天页头部「搜索」按钮打开搜索栏，输入关键词即可全文检索当前用户所有会话的历史消息，点击结果跳转对应会话
- **深色模式**：侧边栏顶部一键切换明/暗主题，CSS 变量驱动全局换肤，偏好自动持久化到 localStorage

### 🔧 Bug 修复

- **修复 POST `/api/chat/stream` 返回 405**：SPA catch-all `GET /{path:path}` 路由覆盖了 API 路径，导致 POST 请求被 405 拦截；现已在 catch-all 中排除 `/api` 前缀

### ⚙️ 配置

- **`RAG_AUTO_SUMMARY_THRESHOLD`**：控制自动摘要触发阈值（默认 10 轮），在 `.env` 中设置

### 📁 涉及文件

| 文件 | 改动 |
|------|------|
| `app/api/v1/admin.py` | 新增，管理员仪表盘 4 个 GET 端点 |
| `app/api/v1/chat.py` | 新增 `GET /search` 消息搜索端点 |
| `app/api/router.py` | 注册 admin 路由 |
| `app/repositories/conversation_repository.py` | 新增 `search_messages` 全文检索方法 |
| `app/services/llm_service.py` | 新增 `summarize` 摘要生成方法 |
| `app/services/memory_service.py` | 新增摘要存取 (`get_summary`/`set_summary`)、轮次计数；`get_recent_context` 自动拼接前文摘要 |
| `app/services/chat_service.py` | 新增 `_maybe_summarize`，在每次消息后检测并触发摘要 |
| `app/core/config.py` | 新增 `auto_summary_threshold` 配置项 |
| `app/main.py` | SPA catch-all 排除 `/api` 路径，修复 405 |
| `frontend/src/App.jsx` | 搜索 UI、深色模式切换、管理员仪表盘弹窗、search/close 图标 |
| `frontend/src/styles.css` | CSS 变量体系（`:root` 浅色 + `[data-theme="dark"]` 深色） |

## v1.5.0（2026-04-24）

### ✨ 新功能

- **角色自定义**：管理员可新建/编辑/删除角色，上传数据集（txt/pdf/md/csv/json/jsonl）自动清洗入库
- **管理员权限系统**：本地配置文件 `app/core/admins.py` 管理管理员账户，知识库上传和角色管理限管理员操作
- **流式回复（SSE）**：大模型回复逐字流式输出，实时更新聊天界面
- **聊天记录导出**：一键导出当前对话为 Markdown 文件

## v1.2.0（2026-04-24）

### ✨ 新功能

- **屏蔽词管理**：支持配置屏蔽词列表，大模型回复或用户提问中命中屏蔽词时，直接返回"抱歉，我无法回答这个问题。"
- **用户输入检测**：用户提问包含屏蔽词时，会话正常创建、消息正常保存，但不调用大模型，节省 token
- **大模型回复检测**：大模型生成的回复包含屏蔽词时，替换为拒绝回复
- **历史记录过滤**：加载聊天历史时，旧消息中包含屏蔽词的 AI 回复也会被过滤

### 📝 使用方式

在 `app/core/blocked_words.py` 中直接编辑 `BLOCKED_WORDS` 列表，重启后端生效：

```python
BLOCKED_WORDS: list[str] = [
    "屏蔽词1",
    "屏蔽词2",
]
```

### 📁 涉及文件

| 文件 | 改动 |
|------|------|
| `app/core/blocked_words.py` | 新增，屏蔽词配置列表 |
| `app/services/chat_service.py` | 新增 `_filter_blocked_words` / `_contains_blocked_word` 方法，集成到发送和历史读取流程 |

---

## v1.1.0（2026-04-24）

### 🔧 Bug 修复

- **修复消息发送 405 错误**：统一 FastAPI 路由注册，支持带/不带尾斜杠的请求路径
- **修复数据库 schema 不一致**：新增自动迁移机制，启动时检测并补齐 `conversation` 表缺失的 `title`、`preview`、`updated_at` 列
- **修复 LLM 连接失败**：切换至硅基流动云 API，绕过本地 Ollama 依赖；禁用系统代理避免 `httpx.ConnectError`
- **修复 Pydantic 验证错误**：`preview`/`title` 为 NULL 时不再报 `ValidationError`
- **修复切换会话消息串台**：切换会话时立即清空消息列表，加载历史时无论是否为空都更新显示
- **修复会话记忆共享**：每个会话拥有独立的 LLM 上下文记忆，互不干扰

### ✨ 新功能

- **会话删除**：右侧会话列表每条记录支持一键删除，同时清除关联的聊天消息
- **会话重命名**：点击「重命名」按钮可内联编辑会话标题，回车确认 / Esc 取消
- **会话隔离**：发送消息时携带 `conversation_id`，后端按会话维度存储消息和管理记忆

### 🎨 界面优化

- **布局固定**：左右侧边栏和底部输入框不再随聊天内容滚动，只有消息区域可滚
- **会话列表样式**：每条会话卡片式垂直排列，选中项高亮，列表撑满右侧栏可用空间
- **操作按钮**：会话卡片底部显示「重命名」「删除」按钮，配色随选中状态自适应

### 🧹 代码清理

- 合并重复路由函数（`send_chat_slash`、`create_conversation_slash`）为堆叠装饰器
- 移除调试端点 `/_debug`
- 移除 `main.py` 中未使用的前端进程管理代码（`stop_frontend_dev_server`）
- 移除冗余 import（`atexit`、`os`、`signal`、行内重复 `HTTPException`）
- 移除 CSS 中重复的 `.chat-stack` 规则、未使用的 `.sidebar-handle` 和 `@keyframes contentFade`

### 📁 涉及文件

| 文件 | 改动 |
|------|------|
| `app/api/v1/chat.py` | 路由整理 + 新增 DELETE/PATCH 端点 |
| `app/schemas/chat.py` | `ChatRequest` 加 `conversation_id`；加 `RenameConversationRequest` |
| `app/services/chat_service.py` | 会话隔离逻辑 + 删除/重命名方法 |
| `app/services/llm_service.py` | 禁用系统代理 `trust_env=False` |
| `app/services/memory_service.py` | Redis key 加 `conversation_id` |
| `app/repositories/conversation_repository.py` | 新增 `delete_conversation` |
| `app/db/init_db.py` | 自动 schema 迁移 |
| `main.py` | 清理无用代码 |
| `.env` | LLM 切换至硅基流动 |
| `frontend/src/App.jsx` | 会话操作 UI + 消息隔离 |
| `frontend/src/styles.css` | 布局固定 + 会话列表样式 |
