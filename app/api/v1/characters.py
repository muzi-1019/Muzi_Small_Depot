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
    repository: CharacterRepository = Depends(get_character_repository),  # 注入角色仓库
) -> list[CharacterOut]:
    """获取所有角色列表接口（所有已登录用户均可调用）"""
    return repository.list_characters()  # 查询全部角色并按响应模型返回


@router.post("", response_model=CharacterOut)
def create_character(
    payload: CharacterCreate,  # 创建角色请求体
    admin_id: int = Depends(require_admin),  # 管理员校验，非管理员会在依赖中被拒绝
    repository: CharacterRepository = Depends(get_character_repository),  # 注入角色仓库
) -> CharacterOut:
    """创建新角色接口（仅管理员可操作）"""
    row = repository.create(
        name=payload.name,  # 角色名称
        role_type=payload.role_type,  # 角色类型
        domain=payload.domain,  # 角色所属领域
        persona=payload.persona,  # 角色人设
        prompt_template=payload.prompt_template,  # 角色提示词模板
    )  # 创建角色数据库记录
    return repository._to_schema(row)  # 转换为 API 输出结构


@router.patch("/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: int,  # 路径参数：要更新的角色 ID
    payload: CharacterUpdate,  # 请求体：只包含需要更新的字段
    admin_id: int = Depends(require_admin),  # 管理员校验
    repository: CharacterRepository = Depends(get_character_repository),  # 注入角色仓库
) -> CharacterOut:
    """更新角色信息接口（仅管理员可操作）"""
    updates = payload.model_dump(exclude_none=True)  # 只提取非空字段
    if not updates:  # 如果用户没有传任何有效更新字段
        raise HTTPException(status_code=400, detail="没有需要更新的字段")  # 返回 400，避免执行空更新
    row = repository.update(character_id, **updates)  # 调用仓库更新角色
    if not row:  # 如果角色不存在
        raise HTTPException(status_code=404, detail="角色不存在")  # 返回 404
    return repository._to_schema(row)  # 返回更新后的角色信息


@router.delete("/{character_id}")
def delete_character(
    character_id: int,  # 路径参数：要删除的角色 ID
    admin_id: int = Depends(require_admin),  # 管理员校验
    repository: CharacterRepository = Depends(get_character_repository),  # 注入角色仓库
):
    """删除角色接口（仅管理员可操作，会级联归档相关会话和消息）"""
    if not repository.delete(character_id):  # 删除失败通常表示角色不存在
        raise HTTPException(status_code=404, detail="角色不存在")  # 返回 404
    return {"code": 200, "message": "角色已删除"}  # 返回删除成功信息


@router.post("/{character_id}/dataset")
async def upload_dataset(
    character_id: int,  # 路径参数：目标角色 ID
    file: UploadFile = File(...),  # 上传的数据集文件
    admin_id: int = Depends(require_admin),  # 管理员校验
    repository: CharacterRepository = Depends(get_character_repository),  # 注入角色仓库
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),  # 注入知识服务，保留依赖以便扩展统一上传逻辑
):
    """
    上传数据集文件接口（仅管理员可操作）。
    支持 txt/pdf/md/csv/json/jsonl 格式。
    上传后会自动清洗文本内容，PDF 文件还会自动解析并写入向量知识库。
    """
    char = repository.get_by_id(character_id)  # 先确认目标角色存在
    if not char:  # 如果角色不存在
        raise HTTPException(status_code=404, detail="角色不存在")  # 返回 404

    raw_name = file.filename or "dataset.txt"              # 原始文件名
    suffix = Path(raw_name).suffix.lower()                 # 文件扩展名
    if suffix not in {".txt", ".pdf", ".md", ".csv", ".json", ".jsonl"}:  # 校验扩展名是否在允许范围内
        raise HTTPException(status_code=400, detail="支持 txt/pdf/md/csv/json/jsonl 格式")  # 不支持的格式返回 400

    body = await file.read()                               # 读取文件内容
    if not body:  # 如果文件内容为空
        raise HTTPException(status_code=400, detail="空文件")  # 拒绝空文件上传

    # 保存原始文件到磁盘
    base_dir = Path(settings.upload_dir) / f"character_{character_id}"  # 每个角色单独目录存放数据集
    base_dir.mkdir(parents=True, exist_ok=True)  # 自动创建目录，parents=True 支持多级目录
    stored = f"{uuid.uuid4().hex}{suffix}"                 # 生成唯一文件名
    dest = base_dir / stored  # 原始文件最终保存路径
    dest.write_bytes(body)  # 将上传内容写入磁盘

    # 对文本内容进行清洗（去除多余空白、格式化等）
    decoded_text, detected_encoding = _decode_text_body(body)  # 自动尝试多种编码解码文本
    cleaned_text = _clean_dataset(decoded_text, suffix)  # 根据文件类型进行文本清洗
    cleaned_path = base_dir / f"{uuid.uuid4().hex}_cleaned.txt"  # 清洗后文本文件路径
    cleaned_path.write_text(cleaned_text, encoding="utf-8")  # 统一以 UTF-8 保存清洗结果

    vector_rows = 0  # 记录本次写入向量库的 chunk 数量
    from app.services.pdf_ingest_service import PDFIngestService  # 局部导入，避免模块加载时提前初始化重依赖
    try:  # 向量入库失败不阻断文件上传结果返回
        ingest_service = PDFIngestService()  # 创建 PDF/文本入库服务
        if suffix == ".pdf":  # PDF 文件需要走 PDF 专用解析逻辑
            vector_rows = ingest_service.ingest_file(character_id, dest.resolve())  # 解析原始 PDF 并写入 Milvus
        else:
            vector_rows = ingest_service.ingest_text(character_id, raw_name, cleaned_text)  # 非 PDF 直接把清洗文本切分入库
    except Exception:  # 捕获解析/embedding/Milvus 异常
        pass  # 当前接口仍返回上传成功，但 vector_rows 保持为 0

    return {
        "code": 200,  # 业务状态码
        "message": "数据集已上传并清洗",  # 操作结果提示
        "original_file": raw_name,  # 原始文件名
        "cleaned_file": str(cleaned_path.name),  # 清洗后的文本文件名
        "cleaned_chars": len(cleaned_text),  # 清洗后字符数
        "detected_encoding": detected_encoding,  # 自动检测/使用的编码
        "vector_rows": vector_rows,  # 写入向量库的 chunk 数量
    }  # 返回上传、清洗和入库统计


def _decode_text_body(body: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "gb2312"):  # 按常见中英文编码顺序尝试解码
        try:  # 单个编码尝试可能失败
            return body.decode(encoding), encoding  # 解码成功则返回文本和编码名
        except UnicodeDecodeError:  # 当前编码无法解析该文件
            continue  # 尝试下一个编码
    return body.decode("utf-8", errors="replace"), "utf-8-replace"  # 最后兜底：替换非法字符，保证流程不中断


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

    if suffix in {".csv"}:  # CSV 文件通常按行保存记录
        # CSV：去除空行
        lines = text.strip().split("\n")  # 按行切分
        cleaned = [line.strip() for line in lines if line.strip()]  # 去除空行并清理每行首尾空白
        text = "\n".join(cleaned)  # 重新拼接为纯文本
    elif suffix in {".json", ".jsonl"}:  # JSON/JSONL 需要提取字段值
        # JSON/JSONL：逐行解析，提取所有字段值拼接为纯文本
        import json as _json
        lines = text.strip().split("\n")  # JSONL 按行处理，普通 JSON 也可作为单行处理
        items = []  # 保存从 JSON 中抽取出的文本片段
        for line in lines:  # 遍历每一行 JSON 文本
            line = line.strip()  # 去除首尾空白
            if not line:  # 空行没有意义
                continue  # 跳过空行
            try:  # 尝试按 JSON 解析当前行
                obj = _json.loads(line)  # 解析 JSON 对象
                if isinstance(obj, dict):  # 如果是字典
                    items.append(" ".join(str(v) for v in obj.values() if v))  # 拼接字典所有非空值
                elif isinstance(obj, list):  # 如果是列表
                    for item in obj:  # 遍历列表元素
                        if isinstance(item, dict):  # 列表元素是字典时
                            items.append(" ".join(str(v) for v in item.values() if v))  # 拼接字典值
                        else:
                            items.append(str(item))  # 非字典元素直接转字符串
            except _json.JSONDecodeError:  # 当前行不是合法 JSON
                items.append(line)  # 保留原始文本，避免数据丢失
        text = "\n".join(items)  # 将所有抽取文本拼接为多行纯文本

    text = re.sub(r"[^\S\n]+", " ", text)                   # 最终清理：合并非换行空白
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if len(line) > 1]        # 过滤过短的行
    return "\n".join(lines).strip()
