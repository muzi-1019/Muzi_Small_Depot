# HTTP JSON 接口说明

> 机器可读的最新契约请以运行中的 OpenAPI 为准：`http://127.0.0.1:8000/docs` 或 `/openapi.json`。

## 通用约定

- `Content-Type: application/json`（文件上传接口除外）。
- 业务响应多数包裹为 `{ "code": 200, "message": "success", "data": ... }`。

## 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务存活探测 |

## 认证与用户偏好

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | 注册；请求体 `{ account, password, nickname }` |
| POST | `/api/auth/login` | 登录；返回 `access_token` 与 `user_id` |
| PATCH | `/api/auth/preferences` | 更新偏好 `{ user_id, preferred_character_ids: number[] }` |

## 角色

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/characters` | 列出系统角色 |

## 对话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | 发送消息（与 `/api/chat/send` 等价） |
| POST | `/api/chat/send` | 同上 |
| GET | `/api/chat/history` | 查询参数：`user_id`、`character_id`、`limit` |

**聊天请求体示例**

```json
{
  "user_id": 1,
  "character_id": 2,
  "question": "高血压饮食要注意什么？"
}
```

## 知识库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/knowledge/upload` | `multipart/form-data`：`character_id`（字段）+ `file`（文件，限 txt/pdf/md） |
| GET | `/api/knowledge/list` | 查询参数：`character_id` |

## 典型错误码

| HTTP | 场景 |
| --- | --- |
| 400 | 账户已存在、空文件、非法扩展名 |
| 401 | 登录失败 |
| 404 | 用户或角色不存在 |
| 409 | 超过并发角色上限（短期记忆活跃控制） |
