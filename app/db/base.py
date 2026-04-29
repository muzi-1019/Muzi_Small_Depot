"""
本文件的作用：定义 SQLAlchemy ORM 的基类。
所有数据库表的模型类（如 User、Character、ChatMessage 等）都要继承这个 Base 类。
SQLAlchemy 会通过 Base 自动追踪所有模型类，在初始化时自动创建对应的数据库表。
"""

from sqlalchemy.orm import DeclarativeBase  # SQLAlchemy 提供的声明式基类


class Base(DeclarativeBase):
    """所有数据库模型的基类，继承它的类会自动映射为数据库中的一张表"""
    pass
