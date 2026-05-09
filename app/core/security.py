"""
本文件的作用：用户密码加密与登录令牌（Token）管理。
提供三个核心安全功能：
1. 把用户明文密码加密成哈希值存入数据库（注册时使用）
2. 校验用户输入的密码是否与数据库中的哈希值匹配（登录时使用）
3. 生成 JWT 登录令牌，用户登录成功后返回给前端，后续每次请求都携带此令牌证明身份
"""

from datetime import datetime, timedelta, timezone  # 日期时间工具，用于计算 Token 过期时间

from jose import jwt                    # python-jose 库，用于生成和解析 JWT（JSON Web Token）令牌
from passlib.context import CryptContext  # passlib 库，用于密码的安全加密和验证

from app.core.config import settings  # 导入全局配置，获取 JWT 密钥、算法、过期时间等参数

# 创建密码加密上下文，使用 pbkdf2_sha256 算法（一种安全的单向哈希算法，无法反向破解出明文密码）。
# 为什么不明文存储密码：
# - 一旦数据库泄露，明文密码会直接暴露用户账号安全；
# - 哈希是单向的，后端只需要验证“输入密码是否能得到同样的哈希结果”，不需要知道原密码；
# - passlib 统一封装了加盐、迭代次数和算法升级，比自己手写哈希更可靠。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    """将用户的明文密码加密为哈希值（用于注册时存入数据库）"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验用户输入的明文密码是否与数据库中存储的哈希值匹配（用于登录验证）"""
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(user_id: int, account: str) -> str:
    """
    生成 JWT 登录令牌（Token）。
    - user_id 和 account 会被编码到 Token 中，后端可以从 Token 中解析出用户身份
    - expire 是过期时间，超过这个时间 Token 自动失效，用户需要重新登录
    为什么使用 JWT：
    - JWT 是无状态令牌，后端不需要为每个登录用户维护 session 表，适合前后端分离项目；
    - 前端每次请求只需携带 Authorization: Bearer <token>，API 网关和接口都容易统一校验；
    - 相比传统 Cookie Session，移动端/桌面端/浏览器都可以用同一套认证方式。
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)  # 计算过期时间点
    payload = {"sub": str(user_id), "account": account, "exp": expire}  # Token 中携带的数据：用户ID、账号、过期时间
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)  # 用密钥加密生成 Token 字符串
