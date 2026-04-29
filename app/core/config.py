"""
本文件的作用：项目全局配置中心。
所有后端运行需要的参数（数据库地址、大模型密钥、向量库维度等）都在这里集中定义。
参数的实际值会从项目根目录的 .env 文件中自动读取，如果 .env 中没有写，则使用这里的默认值。
其他所有 Python 文件通过 `from app.core.config import settings` 来获取配置。
"""

from pathlib import Path  # Python 内置的路径处理工具，用于拼接文件路径

from pydantic_settings import BaseSettings, SettingsConfigDict  # pydantic-settings 库，自动从 .env 文件读取环境变量并校验类型


class Settings(BaseSettings):
    """全局配置类，每个属性就是一个配置项，冒号后面是默认值"""

    # ==================== 应用基础信息 ====================
    app_name: str = "RAG Role-Play System"  # 应用名称，显示在 API 文档标题中
    app_env: str = "dev"                    # 运行环境：dev（开发）/ prod（生产）
    app_debug: bool = True                  # 是否开启调试模式，开启后报错信息更详细

    # ==================== 数据库连接地址 ====================
    mysql_dsn: str = "mysql+pymysql://root:root@localhost:3306/rag_roleplay"  # MySQL 数据库连接字符串（用户名:密码@地址:端口/库名）
    redis_url: str = "redis://localhost:6379/0"                                # Redis 缓存数据库地址，用于存储短期对话记忆
    milvus_uri: str = "http://192.168.35.187:19530"                            # Milvus 向量数据库的访问地址，用于存储和检索 PDF 知识的向量
    milvus_db: str = "default"                                                 # Milvus 中使用的数据库名
    milvus_collection: str = "character_knowledge"                             # Milvus 中存储向量的集合名（类似 MySQL 的表名）
    milvus_enabled: bool = True                                                # 是否启用 Milvus 向量检索功能
    milvus_dim: int = 768                                                      # 向量维度，必须与 embedding 模型输出的维度一致（如 bge-large-zh 输出 1024 维）

    # ==================== 大模型 / AI 配置 ====================
    llm_provider: str = "mock"                                      # 大模型提供商：mock（模拟回复）/ siliconflow（硅基流动云端 API）/ openai 等
    llm_model_name: str = "mock-model"                              # 大模型的模型名称，如 deepseek-ai/DeepSeek-V3
    embedding_model_name: str = "bge-small"                         # 文本向量化模型名称，用于把文字转换成数字向量以便检索
    openai_api_base: str = "https://api.openai.com/v1"              # 大模型 API 的基础地址（兼容 OpenAI 格式的接口地址）
    openai_api_key: str = ""                                        # 大模型 API 的密钥（鉴权用，类似密码）
    siliconflow_api_base: str = "https://api.siliconflow.cn/v1"     # 硅基流动平台的 API 地址（备用）

    # ==================== 用户登录鉴权（JWT） ====================
    jwt_secret: str = "change-me-in-production"  # JWT 签名密钥，用于加密和验证用户登录 Token，生产环境必须改成随机字符串
    jwt_algorithm: str = "HS256"                 # JWT 加密算法
    jwt_expire_minutes: int = 60 * 24            # 登录 Token 有效期，单位分钟（默认 24 小时）

    # ==================== 对话与检索参数 ====================
    short_memory_rounds: int = 20              # 短期记忆保留的最近对话轮数（每轮包含一问一答）
    auto_summary_threshold: int = 10           # 自动生成对话摘要的触发阈值（每累积多少轮对话就自动总结一次）
    retrieval_top_k: int = 8                   # 向量检索时返回最相关的前 K 个文档片段
    rerank_top_k: int = 5                      # 重排序后保留的前 K 个最终结果
    max_concurrent_roles_per_user: int = 20    # 每个用户最多同时与多少个角色进行对话
    active_role_idle_seconds: int = 3600       # 角色会话空闲超时时间（秒），超过此时间视为不活跃，可被新角色替换

    # ==================== 文件存储路径 ====================
    upload_dir: str = "uploads"                                            # 用户上传文件的存储目录
    data_dir: str = str(Path(__file__).resolve().parents[2] / "data")      # 项目内置数据文件目录（如预置 PDF 等）

    # ==================== 跨域访问白名单（CORS） ====================
    # 浏览器安全策略要求：前端和后端如果不在同一个地址，需要配置允许跨域访问
    cors_allowed_origins: list[str] = [
        "http://localhost:5173",       # Vite 开发服务器默认地址
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:5177",
        "http://127.0.0.1:5177",
        "https://frp-six.com",        # 外网穿透地址
        "https://frp-six.com:22552",
    ]
    # 正则匹配的跨域白名单规则，可以更灵活地匹配多个端口和子域名
    cors_allow_origin_regex: str = r"^(http://localhost:517\d+|http://127\.0\.0\.1:517\d+|https://([a-zA-Z0-9-]+\.)*frp-six\.com(:\d+)?)$"

    # ==================== 配置加载规则 ====================
    # 告诉 pydantic-settings：从 .env 文件读取配置，所有环境变量以 RAG_ 为前缀
    # 例如 .env 中 RAG_MYSQL_DSN=xxx 会自动赋值给上面的 mysql_dsn
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_")


# 创建全局唯一的配置实例，其他文件导入这个 settings 对象即可使用所有配置
settings = Settings()
