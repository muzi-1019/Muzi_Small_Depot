"""
本文件的作用：用户认证相关的 API 接口。
提供以下端点：
- POST /auth/register  —— 用户注册（创建新账号）
- POST /auth/login     —— 用户登录（验证密码并返回 JWT Token）
- PATCH /auth/preferences —— 更新用户偏好设置（如常用角色列表）
- GET /auth/me         —— 获取当前登录用户信息
"""

from fastapi import APIRouter, Depends, HTTPException  # FastAPI 核心组件

from app.core.admins import ADMIN_ACCOUNTS                           # 管理员账号列表
from app.core.deps import get_current_user_id, get_user_repository   # 依赖注入函数
from app.core.security import create_access_token, hash_password, verify_password  # 安全工具
from app.repositories.user_repository import UserRepository           # 用户数据访问层
from app.schemas.auth import (                                        # 请求/响应数据结构
    LoginRequest,
    LoginResponse,
    PreferenceRequest,
    PreferenceResponse,
    RegisterRequest,
    RegisterResponse,
    UserOut,
)

router = APIRouter()  # 创建认证模块的路由器


@router.post("/register", response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    user_repository: UserRepository = Depends(get_user_repository),
) -> RegisterResponse:
    """用户注册接口：检查账号是否已存在，不存在则创建新用户"""
    if user_repository.get_by_account(payload.account):  # 检查账号是否重复
        raise HTTPException(status_code=400, detail="账户已存在")
    user = user_repository.create_user(
        account=payload.account,
        password_hash=hash_password(payload.password),  # 对密码进行加密后存储
    )
    return RegisterResponse(data=UserRepository.to_out(user))


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    user_repository: UserRepository = Depends(get_user_repository),
) -> LoginResponse:
    """用户登录接口：验证账号密码，成功后返回 JWT Token"""
    user = user_repository.get_by_account(payload.account)  # 根据账号查找用户
    if not user or not verify_password(payload.password, user.password_hash):  # 验证密码
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token = create_access_token(user.id, user.account)  # 生成 JWT 登录令牌
    return LoginResponse(access_token=token, user_id=user.id)


@router.patch("/preferences", response_model=PreferenceResponse)
def update_preferences(
    payload: PreferenceRequest,
    user_repository: UserRepository = Depends(get_user_repository),
) -> PreferenceResponse:
    """更新用户偏好设置接口：目前用于保存用户常用的角色列表"""
    user = user_repository.get_by_id(payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user_repository.set_preferred_characters(payload.user_id, payload.preferred_character_ids)  # 保存偏好角色
    user = user_repository.get_by_id(payload.user_id)
    assert user is not None
    return PreferenceResponse(data=UserRepository.to_out(user))


@router.get("/me")
def get_me(
    current_user_id: int = Depends(get_current_user_id),
    user_repository: UserRepository = Depends(get_user_repository),
):
    """获取当前登录用户信息接口：返回用户ID、账号和是否为管理员"""
    user = user_repository.get_by_id(current_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user_id": user.id, "account": user.account, "is_admin": user.account in ADMIN_ACCOUNTS}
