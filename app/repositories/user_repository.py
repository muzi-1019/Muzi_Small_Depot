"""
本文件的作用：用户数据访问层（Repository）。
封装了所有与用户表（user）相关的数据库操作，包括：
- 按账号查询用户、按ID查询用户
- 创建新用户（注册）
- 设置用户的角色偏好
- 将数据库模型转换为 API 输出格式
"""

import json  # JSON 解析工具，用于处理用户偏好的角色ID列表

from sqlalchemy import select        # SQLAlchemy 查询构造器
from sqlalchemy.orm import Session   # 数据库会话

from app.db.models import User       # 用户数据库模型
from app.schemas.auth import UserOut  # 用户输出格式定义


class UserRepository:
    """用户数据访问类：所有用户相关的数据库读写操作都在这里"""

    def __init__(self, db: Session) -> None:
        self.db = db  # 保存数据库会话，后续所有操作都通过它执行

    def get_by_account(self, account: str) -> User | None:
        """根据账号查询用户（用于登录验证和注册重复检查）"""
        return self.db.scalar(select(User).where(User.account == account))

    def get_by_id(self, user_id: int) -> User | None:
        """根据用户ID查询用户（用于获取用户信息）"""
        return self.db.get(User, user_id)

    def create_user(self, account: str, password_hash: str, nickname: str = "新用户") -> User:
        """创建新用户并写入数据库（用于注册）"""
        user = User(account=account, password_hash=password_hash, nickname=nickname, preferred_character_ids="[]")
        self.db.add(user)       # 将用户对象添加到会话中
        self.db.commit()        # 提交事务，写入数据库
        self.db.refresh(user)   # 刷新对象，获取数据库自动生成的 ID
        return user

    def set_preferred_characters(self, user_id: int, character_ids: list[int]) -> User | None:
        """设置用户偏好的角色列表（将角色ID列表转为 JSON 字符串存入数据库）"""
        user = self.db.get(User, user_id)
        if not user:
            return None
        user.preferred_character_ids = json.dumps(character_ids, ensure_ascii=False)  # 列表转 JSON 字符串
        self.db.commit()
        self.db.refresh(user)
        return user

    @staticmethod
    def to_out(user: User) -> UserOut:
        """将数据库 User 模型对象转换为 API 输出格式（UserOut）"""
        try:
            prefs = json.loads(user.preferred_character_ids or "[]")  # 将 JSON 字符串解析回列表
        except json.JSONDecodeError:
            prefs = []  # 解析失败则返回空列表
        return UserOut(
            id=user.id,
            account=user.account,
            nickname=user.nickname,
            preferred_character_ids=prefs,
        )
