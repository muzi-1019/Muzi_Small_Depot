"""
本文件的作用：对话记忆服务（基于 Redis 实现）。
管理每个用户与每个角色之间的短期对话记忆，具体功能包括：
1. 存储最近 N 轮对话上下文（发送给大模型时作为"记忆"，让 AI 记住之前聊了什么）
2. 管理活跃角色槽位（限制每个用户同时对话的角色数量，防止资源浪费）
3. 存储和获取对话摘要（当对话太长时，自动总结前文以节省 Token）

为什么用 Redis？因为对话记忆是临时数据，不需要永久保存，Redis 读写速度极快且支持自动过期。
"""

import time  # 时间戳工具，用于记录角色活跃时间

import redis               # Redis 客户端库
from fastapi import HTTPException  # HTTP 异常类

from app.core.config import settings  # 全局配置


class MemoryService:
    """对话记忆服务：基于 Redis 管理短期对话上下文和角色并发控制"""

    def __init__(self, max_rounds: int | None = None) -> None:
        # 最多保留的对话轮数，超过的会被自动截断
        self.max_rounds = max_rounds if max_rounds is not None else settings.short_memory_rounds
        # 创建 Redis 客户端连接（decode_responses=True 表示自动将字节解码为字符串）
        self.redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    @staticmethod
    def _session_key(user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """生成 Redis 中存储对话记忆的键名，格式：chat:session:用户ID:角色ID:会话ID"""
        if conversation_id:
            return f"chat:session:{user_id}:{character_id}:{conversation_id}"
        return f"chat:session:{user_id}:{character_id}"

    def ensure_concurrent_role_slot(self, user_id: int, character_id: int) -> None:
        """
        检查并确保用户有可用的角色对话槽位。
        每个用户最多同时与 max_concurrent_roles_per_user 个角色对话。
        超过空闲时间的角色会被自动清理，腾出槽位。
        """
        key = f"chat:active_roles:{user_id}"  # 存储用户当前活跃角色的 Redis 键
        now = time.time()                       # 当前时间戳
        idle = settings.active_role_idle_seconds  # 空闲超时时间

        # 第一步：清理超时的角色（超过空闲时间的视为不活跃，自动移除）
        data = self.redis_client.hgetall(key)  # 获取用户所有活跃角色及其最后活跃时间
        for cid, ts in list(data.items()):
            try:
                if now - float(ts) > idle:  # 如果距离上次活跃已超过空闲时间
                    self.redis_client.hdel(key, cid)  # 从活跃列表中移除
            except ValueError:
                self.redis_client.hdel(key, cid)  # 时间戳格式错误也移除

        # 第二步：检查当前角色是否已在活跃列表中
        data = self.redis_client.hgetall(key)  # 重新获取清理后的列表
        sid = str(character_id)
        if sid in data:  # 如果当前角色已在列表中，更新活跃时间即可
            self.redis_client.hset(key, sid, str(now))
            self.redis_client.expire(key, int(idle * 4))  # 延长 Redis 键的过期时间
            return

        # 第三步：如果是新角色，检查槽位是否已满
        if len(data) >= settings.max_concurrent_roles_per_user:
            raise HTTPException(
                status_code=409,
                detail="每个用户最多同时与3个角色对话；请等待其它角色会话冷却结束后再开启新角色。",
            )

        # 第四步：有空闲槽位，将新角色加入活跃列表
        self.redis_client.hset(key, sid, str(now))
        self.redis_client.expire(key, int(idle * 4))

    def append_round(self, user_id: int, character_id: int, human: str, ai: str, conversation_id: int | None = None) -> None:
        """
        将一轮对话（用户消息 + AI 回复）追加到 Redis 记忆列表中。
        使用 ltrim 保持列表长度不超过 max_rounds * 2 条（每轮2条：一问一答）。
        """
        key = self._session_key(user_id, character_id, conversation_id)
        with self.redis_client.pipeline() as pipe:  # 使用管道批量执行，提高性能
            pipe.rpush(key, f"用户: {human}", f"AI: {ai}")      # 追加到列表末尾
            pipe.ltrim(key, -self.max_rounds * 2, -1)             # 只保留最新的 N 轮
            pipe.execute()

    def get_recent_context(self, user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """
        获取最近的对话上下文（发送给大模型的"记忆"部分）。
        如果有前文摘要，会拼接在最前面，然后是最近的对话记录。
        """
        key = self._session_key(user_id, character_id, conversation_id)
        items = self.redis_client.lrange(key, 0, -1)  # 获取所有对话记录
        summary = self.get_summary(user_id, character_id, conversation_id)  # 获取前文摘要
        parts = []
        if summary:
            parts.append(f"[前文摘要] {summary}")  # 摘要放在最前面
        parts.extend(items)  # 后面跟上最近的对话
        return "\n".join(parts)

    @staticmethod
    def _summary_key(user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """生成 Redis 中存储对话摘要的键名"""
        if conversation_id:
            return f"chat:summary:{user_id}:{character_id}:{conversation_id}"
        return f"chat:summary:{user_id}:{character_id}"

    def get_summary(self, user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """获取对话的前文摘要（由大模型自动生成的简短总结）"""
        key = self._summary_key(user_id, character_id, conversation_id)
        return self.redis_client.get(key) or ""

    def set_summary(self, user_id: int, character_id: int, summary: str, conversation_id: int | None = None) -> None:
        """保存对话摘要到 Redis（带过期时间，过期后自动删除）"""
        key = self._summary_key(user_id, character_id, conversation_id)
        self.redis_client.set(key, summary, ex=settings.active_role_idle_seconds * 4)

    def get_round_count(self, user_id: int, character_id: int, conversation_id: int | None = None) -> int:
        """获取当前对话的轮数（Redis 列表长度 / 2，因为每轮包含一问一答两条记录）"""
        key = self._session_key(user_id, character_id, conversation_id)
        return self.redis_client.llen(key) // 2
