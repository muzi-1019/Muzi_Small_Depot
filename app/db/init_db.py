"""
本文件的作用：数据库初始化脚本。
在后端启动时自动执行，完成以下三件事：
1. 根据 models.py 中定义的表结构，自动创建 MySQL 中还不存在的表
2. 检查已有表的字段是否完整，如果缺少新增的字段则自动补齐（类似数据库迁移）
3. 如果角色表为空，插入三个默认角色（虚拟朋友、高血压医生、民事律师）作为初始数据
"""

from sqlalchemy import select, text     # select 用于查询，text 用于执行原生 SQL
from sqlalchemy.orm import Session      # 数据库会话

from app.db.base import Base            # ORM 基类，包含所有表的元信息
from app.db.models import Character     # 角色模型，用于插入初始角色数据
from app.db.session import engine       # 数据库引擎


def init_db() -> None:
    """
    数据库初始化入口函数，在应用启动时被 app/main.py 调用。
    执行顺序：建表 → 补字段 → 插入种子数据。
    """
    Base.metadata.create_all(bind=engine)  # 根据所有模型类自动创建 MySQL 表（已存在的表不会重复创建）
    _ensure_schema()                       # 检查并补齐可能缺失的字段（增量迁移）
    with Session(engine) as session:       # 打开数据库会话
        _seed_characters(session)          # 插入默认角色（仅首次运行时）


def _ensure_schema() -> None:
    """
    增量数据库迁移：检查已有表中是否缺少新增的字段，如果缺少就自动用 ALTER TABLE 添加。
    这样即使数据库是旧版本的，更新代码后重启也能自动补齐新字段，无需手动执行 SQL。
    """
    with engine.connect() as conn:
        # ---------- 检查 conversation 表是否存在 ----------
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :name LIMIT 1"
            ),
            {"name": "conversation"},
        ).scalar()
        if not table_exists:  # 表还不存在说明是全新安装，create_all 已经建好了，无需迁移
            return

        # ---------- 获取 conversation 表的现有字段列表 ----------
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :name"
                ),
                {"name": "conversation"},
            ).all()
        }

        # ---------- 检查并添加 conversation 表缺失的字段 ----------
        ddl: list[str] = []
        if "title" not in columns:       # 会话标题字段
            ddl.append("ALTER TABLE conversation ADD COLUMN title VARCHAR(128) NOT NULL DEFAULT '新对话'")
        if "preview" not in columns:     # 消息预览字段
            ddl.append("ALTER TABLE conversation ADD COLUMN preview TEXT")
        if "updated_at" not in columns:  # 最后更新时间字段
            ddl.append("ALTER TABLE conversation ADD COLUMN updated_at DATETIME")

        for stmt in ddl:             # 逐条执行 ALTER TABLE 语句
            conn.execute(text(stmt))
        if ddl:
            conn.commit()            # 有修改才提交事务

        # ---------- 获取 chat_message 表的现有字段列表 ----------
        msg_columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :name"
                ),
                {"name": "chat_message"},
            ).all()
        }

        # ---------- 检查并添加 chat_message 表缺失的字段 ----------
        msg_ddl: list[str] = []
        if "rag_used" not in msg_columns:  # 是否使用了向量检索的标记字段
            msg_ddl.append("ALTER TABLE chat_message ADD COLUMN rag_used TINYINT(1) NOT NULL DEFAULT 0")
        if "sources_json" not in msg_columns:  # 检索知识片段元数据（JSON数组）
            msg_ddl.append("ALTER TABLE chat_message ADD COLUMN sources_json TEXT")
        for stmt in msg_ddl:
            conn.execute(text(stmt))
        if msg_ddl:
            conn.commit()

        # ---------- 获取 archived_chat_message 表的现有字段列表 ----------
        arch_columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :name"
                ),
                {"name": "archived_chat_message"},
            ).all()
        }
        # ---------- 检查并添加 archived_chat_message 表缺失的字段 ----------
        arch_ddl: list[str] = []
        if "sources_json" not in arch_columns:
            arch_ddl.append("ALTER TABLE archived_chat_message ADD COLUMN sources_json TEXT")
        for stmt in arch_ddl:
            conn.execute(text(stmt))
        if arch_ddl:
            conn.commit()


def _seed_characters(session: Session) -> None:
    """
    插入默认角色数据（种子数据）。
    只在角色表完全为空时执行，避免重复插入。
    """
    exists = session.scalar(select(Character.id).limit(1))  # 查询是否已有角色
    if exists:  # 已有数据，跳过
        return

    # 插入三个默认角色
    session.add_all(
        [
            Character(
                name="虚拟朋友",                    # 社交陪伴类角色
                role_type="social",
                domain="日常陪伴",
                persona="温柔、幽默、善解人意",
                prompt_template="社交朋友模板",
                knowledge_base_id="social_default",
            ),
            Character(
                name="高血压专科医生",               # 医疗专业类角色
                role_type="professional",
                domain="医疗",
                persona="严谨、专业、注重风险提示",
                prompt_template="高血压医生模板",
                knowledge_base_id="medical_hypertension",
            ),
            Character(
                name="民事律师",                    # 法律专业类角色
                role_type="professional",
                domain="法律",
                persona="逻辑清晰、依据法条、边界明确",
                prompt_template="民事律师模板",
                knowledge_base_id="law_civil",
            ),
        ]
    )
    session.commit()  # 提交事务，将数据写入数据库
