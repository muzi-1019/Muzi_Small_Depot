"""
本文件的作用：定义 AI 角色相关 API 的请求和响应数据结构。
包括角色的输出格式、创建角色请求、更新角色请求的数据定义。
"""

from pydantic import BaseModel  # Pydantic 数据模型基类


class CharacterOut(BaseModel):
    """角色信息输出格式：返回给前端的角色详情"""
    id: int                           # 角色唯一ID
    name: str                         # 角色名称，如"高血压专科医生"
    role_type: str                    # 角色类型：social（社交）/ professional（专业）/ custom（自定义）
    domain: str                       # 所属领域，如"医疗"、"法律"
    persona: str                      # 人设描述，定义角色的性格和行为风格
    prompt_template: str = ""         # 提示词模板，用于引导大模型的回答方式
    knowledge_base_id: str = ""       # 关联的知识库标识


class CharacterCreate(BaseModel):
    """创建角色请求：管理员创建新角色时提交的数据"""
    name: str                         # 角色名称（必填）
    role_type: str = "custom"         # 角色类型，默认为自定义
    domain: str = ""                  # 所属领域
    persona: str = ""                 # 人设描述
    prompt_template: str = ""         # 提示词模板


class CharacterUpdate(BaseModel):
    """更新角色请求：管理员修改角色信息时提交的数据（所有字段可选，只更新提供的字段）"""
    name: str | None = None           # 新名称（不传则不修改）
    role_type: str | None = None      # 新类型
    domain: str | None = None         # 新领域
    persona: str | None = None        # 新人设
    prompt_template: str | None = None  # 新提示词模板
