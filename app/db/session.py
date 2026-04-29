"""
本文件的作用：创建数据库连接引擎和会话工厂。
- engine：数据库连接引擎，负责管理与 MySQL 的底层连接池
- SessionLocal：会话工厂，每次需要操作数据库时创建一个会话（Session）
- get_db_session：FastAPI 的依赖注入生成器，自动管理会话的创建和关闭
"""

from sqlalchemy import create_engine          # SQLAlchemy 提供的数据库引擎创建函数
from sqlalchemy.orm import Session, sessionmaker  # Session 是数据库操作的核心对象

from app.core.config import settings  # 导入全局配置，获取数据库连接地址

# 创建数据库引擎：根据配置中的 MySQL 连接字符串建立连接池
# pool_pre_ping=True 表示每次从池中取连接前先发一个测试查询，防止连接已断开
engine = create_engine(settings.mysql_dsn, pool_pre_ping=True)

# 创建会话工厂：后续每次调用 SessionLocal() 就会生成一个新的数据库会话
# autoflush=False 和 autocommit=False 表示需要手动控制事务提交
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db_session():
    """
    数据库会话的生成器函数（配合 FastAPI 的 Depends 使用）。
    工作流程：
    1. 创建一个数据库会话
    2. 通过 yield 把会话交给 API 接口使用
    3. API 处理完毕后（不管成功还是失败），自动关闭会话释放连接
    """
    db = SessionLocal()  # 创建新的数据库会话
    try:
        yield db  # 把会话交给调用者使用
    finally:
        db.close()  # 最终一定会关闭会话，释放数据库连接回连接池
