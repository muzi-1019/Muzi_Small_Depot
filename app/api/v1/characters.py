"""
本文件的作用：角色管理相关的 API 接口。
提供以下端点：
- GET    /characters              —— 获取所有角色列表（所有用户可访问）
- POST   /characters              —— 创建新角色（仅管理员）
- PATCH  /characters/{id}         —— 更新角色信息（仅管理员）
- DELETE /characters/{id}         —— 删除角色（仅管理员）
- POST   /characters/{id}/dataset —— 上传数据集文件增强角色知识（仅管理员）
"""

import re                            # 正则表达式，用于文本清洗
import uuid                          # 用于生成唯一文件名
from pathlib import Path              # 文件路径处理

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile  # FastAPI 核心组件

from app.core.config import settings                                              # 全局配置
from app.core.deps import get_character_repository, get_knowledge_service, require_admin  # 依赖注入
from app.repositories.character_repository import CharacterRepository               # 角色数据访问层
from app.schemas.character import CharacterCreate, CharacterOut, CharacterUpdate    # 数据结构
from app.services.knowledge_service import KnowledgeService                        # 知识库服务

router = APIRouter()  # 创建角色管理模块的路由器


@router.get("", response_model=list[CharacterOut])
def list_characters(
    repository: CharacterRepository = Depends(get_character_repository),
) -> list[CharacterOut]:
    """获取所有角色列表接口（所有已登录用户均可调用）"""
    return repository.list_characters()


@router.post("", response_model=CharacterOut)
def create_character(
    payload: CharacterCreate,
    admin_id: int = Depends(require_admin),
    repository: CharacterRepository = Depends(get_character_repository),
) -> CharacterOut:
    """创建新角色接口（仅管理员可操作）"""
    row = repository.create(
        name=payload.name,
        role_type=payload.role_type,
        domain=payload.domain,
        persona=payload.persona,
        prompt_template=payload.prompt_template,
    )
    return repository._to_schema(row)


@router.patch("/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: int,
    payload: CharacterUpdate,
    admin_id: int = Depends(require_admin),
    repository: CharacterRepository = Depends(get_character_repository),
) -> CharacterOut:
    """更新角色信息接口（仅管理员可操作）"""
    updates = payload.model_dump(exclude_none=True)  # 只提取非空字段
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    row = repository.update(character_id, **updates)
    if not row:
        raise HTTPException(status_code=404, detail="角色不存在")
    return repository._to_schema(row)


@router.delete("/{character_id}")
def delete_character(
    character_id: int,
    admin_id: int = Depends(require_admin),
    repository: CharacterRepository = Depends(get_character_repository),
):
    """删除角色接口（仅管理员可操作，会级联归档相关会话和消息）"""
    if not repository.delete(character_id):
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"code": 200, "message": "角色已删除"}


@router.post("/{character_id}/dataset")
async def upload_dataset(
    character_id: int,
    file: UploadFile = File(...),
    admin_id: int = Depends(require_admin),
    repository: CharacterRepository = Depends(get_character_repository),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
):
    """
    上传数据集文件接口（仅管理员可操作）。
    支持 txt/pdf/md/csv/json/jsonl 格式。
    上传后会自动清洗文本内容，PDF 文件还会自动解析并写入向量知识库。
    """
    char = repository.get_by_id(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="角色不存在")

    raw_name = file.filename or "dataset.txt"              # 原始文件名
    suffix = Path(raw_name).suffix.lower()                 # 文件扩展名
    if suffix not in {".txt", ".pdf", ".md", ".csv", ".json", ".jsonl"}:
        raise HTTPException(status_code=400, detail="支持 txt/pdf/md/csv/json/jsonl 格式")

    body = await file.read()                               # 读取文件内容
    if not body:
        raise HTTPException(status_code=400, detail="空文件")

    # 保存原始文件到磁盘
    base_dir = Path(settings.upload_dir) / f"character_{character_id}"
    base_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{suffix}"                 # 生成唯一文件名
    dest = base_dir / stored
    dest.write_bytes(body)

    # 对文本内容进行清洗（去除多余空白、格式化等）
    cleaned_text = _clean_dataset(body.decode("utf-8", errors="ignore"), suffix)
    cleaned_path = base_dir / f"{uuid.uuid4().hex}_cleaned.txt"
    cleaned_path.write_text(cleaned_text, encoding="utf-8")

    # PDF 文件自动解析并写入向量知识库
    if suffix == ".pdf":
        from app.services.pdf_ingest_service import PDFIngestService
        try:
            PDFIngestService().ingest_file(character_id, dest.resolve())
        except Exception:
            pass

    return {
        "code": 200,
        "message": "数据集已上传并清洗",
        "original_file": raw_name,
        "cleaned_file": str(cleaned_path.name),
        "cleaned_chars": len(cleaned_text),
    }


def _clean_dataset(text: str, suffix: str) -> str:
    """
    数据集文本清洗函数：
    - 统一换行符
    - 去除多余空白
    - CSV 格式：去除空行
    - JSON/JSONL 格式：提取字段值拼接为纯文本
    - 过滤过短的行（长度<=1 的行）
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # 统一换行符
    text = re.sub(r"[ \t]+", " ", text)                     # 合并连续空格和制表符
    text = re.sub(r"\n{3,}", "\n\n", text)                  # 连续3个以上换行缩减为2个

    if suffix in {".csv"}:
        # CSV：去除空行
        lines = text.strip().split("\n")
        cleaned = [line.strip() for line in lines if line.strip()]
        text = "\n".join(cleaned)
    elif suffix in {".json", ".jsonl"}:
        # JSON/JSONL：逐行解析，提取所有字段值拼接为纯文本
        import json as _json
        lines = text.strip().split("\n")
        items = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
                if isinstance(obj, dict):
                    items.append(" ".join(str(v) for v in obj.values() if v))
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            items.append(" ".join(str(v) for v in item.values() if v))
                        else:
                            items.append(str(item))
            except _json.JSONDecodeError:
                items.append(line)
        text = "\n".join(items)

    text = re.sub(r"[^\S\n]+", " ", text)                   # 最终清理：合并非换行空白
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if len(line) > 1]        # 过滤过短的行
    return "\n".join(lines).strip()
