"""
本文件的作用：定义用户认证相关 API 的请求和响应数据结构。
包括注册、登录、获取用户信息、设置角色偏好等接口的数据格式定义。
"""

from pydantic import BaseModel, Field  # Pydantic 数据模型基类和字段约束


class LoginRequest(BaseModel):
    """登录请求：用户提交的登录信息"""
    account: str    # 用户账号
    password: str   # 用户密码（明文，后端会与哈希值比对）


class LoginResponse(BaseModel):
    """登录响应：登录成功后返回给前端的数据"""
    access_token: str                  # JWT 登录令牌，前端后续每次请求都要在 Header 中携带
    token_type: str = "bearer"         # 令牌类型，固定为 bearer
    user_id: int = Field(..., description="Registered user id; use as chat.user_id")  # 用户ID


class RegisterRequest(BaseModel):
    """注册请求：新用户注册时提交的信息"""
    account: str = Field(..., min_length=3, max_length=64)    # 账号，3~64 字符
    password: str = Field(..., min_length=6, max_length=128)  # 密码，6~128 字符


class UserOut(BaseModel):
    """用户信息输出格式：返回给前端的用户基本信息"""
    id: int                                    # 用户ID
    account: str                               # 用户账号
    nickname: str                              # 用户昵称
    preferred_character_ids: list[int] = []    # 用户偏好的角色ID列表


class RegisterResponse(BaseModel):
    """注册响应"""
    code: int = 200
    message: str = "success"
    data: UserOut                               # 注册成功后返回用户信息


class PreferenceRequest(BaseModel):
    """设置角色偏好请求"""
    user_id: int                                                             # 用户ID
    preferred_character_ids: list[int] = Field(default_factory=list, max_length=10)  # 偏好角色ID列表（最多10个）


class PreferenceResponse(BaseModel):
    """设置角色偏好响应"""
    code: int = 200
    message: str = "success"
    data: UserOut                               # 更新后的用户信息
