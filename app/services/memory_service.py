"""
本文件的作用：对话记忆服务（基于 Redis 实现）。
管理每个用户与每个角色之间的短期对话记忆，具体功能包括：
1. 存储最近 N 轮对话上下文（发送给大模型时作为"记忆"，让 AI 记住之前聊了什么）
2. 管理活跃角色槽位（限制每个用户同时对话的角色数量，防止资源浪费）
3. 存储和获取对话摘要（当对话太长时，自动总结前文以节省 Token）

为什么用 Redis？因为对话记忆是临时数据，不需要永久保存，Redis 读写速度极快且支持自动过期。
相比把短期记忆全部放 MySQL：
- Redis 更适合高频读写的小块上下文，避免每轮聊天都频繁查关系库；
- 过期时间和滑动窗口天然适合“最近 N 轮对话”这种临时状态；
- MySQL 仍负责消息永久留存，Redis 只承担会话态缓存，两者职责更清晰。
"""

import logging  # 日志模块，用于记录 Redis 状态和降级行为
import time  # 时间戳工具，用于记录角色活跃时间

import redis               # Redis 客户端库
from fastapi import HTTPException  # HTTP 异常类

from app.core.config import settings  # 全局配置

logger = logging.getLogger(__name__)  # 当前模块日志器

_fallback_sessions: dict[str, list[str]] = {}  # Redis 不可用时的进程内会话记忆兜底缓存
_fallback_summaries: dict[str, str] = {}  # Redis 不可用时的进程内摘要兜底缓存


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
        self.redis_available = True  # 标记 Redis 当前是否可用，失败后会切换为 False 避免反复 ping

    def _redis_ok(self) -> bool:
        """检查 Redis 是否可用；不可用时使用内存兜底，避免聊天主流程中断"""
        if not self.redis_available:  # 如果之前已经判断 Redis 不可用
            return False  # 直接返回 False，走内存兜底
        try:  # ping 可能因为连接失败、认证失败、服务不可达而抛错
            self.redis_client.ping()  # 发送 ping 检查 Redis 是否可连接
            return True  # ping 成功表示 Redis 可用
        except redis.RedisError as exc:  # 捕获 Redis 客户端异常
            self.redis_available = False  # 标记为不可用，后续直接降级
            logger.warning("Redis 不可用，MemoryService 已降级为进程内临时记忆: url=%s error=%s", settings.redis_url, exc)
            return False

    @staticmethod
    def _session_key(user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """生成 Redis 中存储对话记忆的键名，格式：chat:session:用户ID:角色ID:会话ID"""
        if conversation_id:  # 如果有会话 ID
            return f"chat:session:{user_id}:{character_id}:{conversation_id}"  # 按用户+角色+会话隔离记忆
        return f"chat:session:{user_id}:{character_id}"  # 兼容旧逻辑：只按用户+角色隔离

    def ensure_concurrent_role_slot(self, user_id: int, character_id: int) -> None:
        """
        检查并确保用户有可用的角色对话槽位。
        每个用户最多同时与 max_concurrent_roles_per_user 个角色对话。
        超过空闲时间的角色会被自动清理，腾出槽位。
        为什么限制活跃角色数量：
        - 每个角色都可能维护独立记忆和上下文，完全不限制会导致 Redis 键和 LLM 上下文膨胀；
        - 对用户体验来说，同时保留最近几个活跃角色足够，冷门角色可在空闲后自动释放；
        - 比直接禁止新会话更友好：系统会先清理超时角色，再判断是否达到上限。
        """
        if not self._redis_ok():
            logger.info("Redis 降级模式：跳过角色并发槽位检查 user_id=%s character_id=%s", user_id, character_id)
            return
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
        sid = str(character_id)  # Redis hash field 使用字符串形式的角色 ID
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
        self.redis_client.hset(key, sid, str(now))  # 写入新角色的最后活跃时间
        self.redis_client.expire(key, int(idle * 4))  # 设置过期时间，长期不活跃自动清理

    def append_round(self, user_id: int, character_id: int, human: str, ai: str, conversation_id: int | None = None) -> None:
        """
        将一轮对话（用户消息 + AI 回复）追加到 Redis 记忆列表中。
        使用 ltrim 保持列表长度不超过 max_rounds * 2 条（每轮2条：一问一答）。
        """
        key = self._session_key(user_id, character_id, conversation_id)
        if not self._redis_ok():
            items = _fallback_sessions.setdefault(key, [])  # 获取或创建内存兜底列表
            items.extend([f"用户: {human}", f"AI: {ai}"])  # 追加用户和 AI 两条消息
            del items[:-self.max_rounds * 2]  # 只保留最后 max_rounds 轮，删除更早内容
            logger.info("Redis 降级模式：已写入临时记忆 key=%s rounds=%d", key, len(items) // 2)
            return
        with self.redis_client.pipeline() as pipe:  # 使用管道批量执行，提高性能
            pipe.rpush(key, f"用户: {human}", f"AI: {ai}")      # 追加到列表末尾
            pipe.ltrim(key, -self.max_rounds * 2, -1)             # 只保留最新的 N 轮
            pipe.execute()  # 一次性执行 rpush 和 ltrim

    def get_recent_context(self, user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """
        获取最近的对话上下文（发送给大模型的"记忆"部分）。
        如果有前文摘要，会拼接在最前面，然后是最近的对话记录。
        """
        key = self._session_key(user_id, character_id, conversation_id)
        if not self._redis_ok():
            items = _fallback_sessions.get(key, [])  # 读取进程内临时记忆
            summary = self.get_summary(user_id, character_id, conversation_id)  # 读取摘要
            parts = []  # 保存最终拼接的上下文片段
            if summary:  # 如果存在前文摘要
                parts.append(f"[前文摘要] {summary}")
            parts.extend(items)
            logger.info("Redis 降级模式：读取临时记忆 key=%s rounds=%d summary=%s", key, len(items) // 2, bool(summary))
            return "\n".join(parts)
        items = self.redis_client.lrange(key, 0, -1)  # 获取所有对话记录
        summary = self.get_summary(user_id, character_id, conversation_id)  # 获取前文摘要
        parts = []  # 保存摘要和最近对话
        if summary:  # 有摘要时放在上下文前面
            parts.append(f"[前文摘要] {summary}")  # 摘要放在最前面
        parts.extend(items)  # 后面跟上最近的对话
        return "\n".join(parts)

    @staticmethod
    def _summary_key(user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """生成 Redis 中存储对话摘要的键名"""
        if conversation_id:  # 如果有会话 ID
            return f"chat:summary:{user_id}:{character_id}:{conversation_id}"  # 摘要按具体会话隔离
        return f"chat:summary:{user_id}:{character_id}"  # 兼容旧逻辑：按用户+角色保存摘要

    def get_summary(self, user_id: int, character_id: int, conversation_id: int | None = None) -> str:
        """获取对话的前文摘要（由大模型自动生成的简短总结）"""
        key = self._summary_key(user_id, character_id, conversation_id)
        if not self._redis_ok():
            logger.info("Redis 降级模式：读取临时摘要 key=%s exists=%s", key, key in _fallback_summaries)
            return _fallback_summaries.get(key, "")
        return self.redis_client.get(key) or ""

    def set_summary(self, user_id: int, character_id: int, summary: str, conversation_id: int | None = None) -> None:
        """保存对话摘要到 Redis（带过期时间，过期后自动删除）"""
        key = self._summary_key(user_id, character_id, conversation_id)
        if not self._redis_ok():
            _fallback_summaries[key] = summary  # 写入进程内摘要兜底缓存
            logger.info("Redis 降级模式：已写入临时摘要 key=%s len=%d", key, len(summary))
            return
        self.redis_client.set(key, summary, ex=settings.active_role_idle_seconds * 4)  # 写入 Redis 并设置过期时间

    def get_round_count(self, user_id: int, character_id: int, conversation_id: int | None = None) -> int:
        """获取当前对话的轮数（Redis 列表长度 / 2，因为每轮包含一问一答两条记录）"""
        key = self._session_key(user_id, character_id, conversation_id)
        if not self._redis_ok():
            count = len(_fallback_sessions.get(key, [])) // 2
            logger.info("Redis 降级模式：读取临时轮数 key=%s count=%d", key, count)
            return count
        return self.redis_client.llen(key) // 2  # Redis 列表长度除以 2 得到对话轮数
