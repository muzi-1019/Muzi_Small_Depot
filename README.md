项目名称
- Role-playing system based on RAG_new（基于RAG的角色扮演对话系统(新)）
项目目的
构建可扮演多种角色（如医生、律师、虚拟朋友等）的聊天机器人 支持多用户、多角色、多轮对话 具备知识库动态更新与RAG检索能力

一、项目整体作用
这是一个“基于 RAG 的角色扮演系统”。
整体基础流程是：
- 用户登录/注册
- 选择角色
- 发送聊天问题
- 后端根据角色与知识库内容进行检索增强生成（RAG）
- 前端展示对话结果、历史记录和知识库信息
- 支持本地启动，也支持通过内网穿透，让外部用户访问

- 2. 技术栈
| 层级 | 技术 |
| --- | --- |
| Web 框架 | FastAPI |
| 关系库 | MySQL |
| 缓存 | Redis |
| 向量库 | Milvus（`pymilvus` 懒加载）--【用到时才加载，不用不加载】 |
| 嵌入模型/重排模型 | BGE-m3 （把句子 / 文档 / 问题变成向量（Embedding）/ BGE-rerank（检索出来的结果太乱，它负责重新排序） |这是给文本做向量、以及给检索结果做精准排序的模型
| 大模型 | 调用在线 API 如deepseek、硅基、千问等在线大模型


## 4. 数据库设计（逻辑）
`user`：账号凭证、昵称、`preferred_character_ids`（JSON 文本）。
`character`：角色定义、提示词模板、`knowledge_base_id`。
`conversation`：`(user_id, character_id)` 唯一会话（应用层 get_or_create），`updated_at` 在消息写入时刷新。
`chat_message`：一问一答。
`knowledge_document`：上传文件元数据与索引状态。
