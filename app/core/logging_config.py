"""
系统日志统一配置。
在 main.py 启动时调用 setup_logging()，所有模块通过 `logging.getLogger(__name__)` 获取 logger。
"""

import logging
import sys
from pathlib import Path


def setup_logging(
    level: int = logging.INFO,
    log_dir: str = "log",
    console: bool = True,
) -> None:
    """
    配置全局日志：
    - 控制台输出：彩色格式，包含时间、级别、logger 名、消息
    - 文件输出：写入 log/app.log（按天轮转可由外部 crontab 处理）
    为什么集中配置 logging，而不是各文件 print：
    - logging 支持 INFO/DEBUG/WARNING 分级，开发时能看详细检索过程，生产时能减少噪声；
    - 统一格式便于定位是哪个模块输出的日志，也便于保存到文件后做问题追踪；
    - 相比 print，logger.exception / exc_info=True 能保留完整堆栈，排查线上错误更可靠。
    """
    # 确保日志目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler，避免重复
    for h in root.handlers[:]:
        root.removeHandler(h)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # 文件 handler
    file_handler = logging.FileHandler(log_path / "app.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 第三方库日志级别调低，避免刷屏。
    # 这些库在 DEBUG 模式下会输出 TCP 握手、HTTP headers、线程检测等大量底层细节，
    # 对业务排查价值不高，反而会淹没 RAG 检索、Rerank、LLM 调用等关键日志。
    for noisy in (
        "uvicorn",          # uvicorn 启动信息
        "uvicorn.access",   # 重复的 HTTP 访问日志（已有 app.access 中间件）
        "httpx",            # httpx 客户端
        "httpcore",         # httpcore TCP/TLS 握手细节
        "pymilvus",         # Milvus SDK
        "numexpr",          # NumExpr 线程检测
        "charset_normalizer",
        "urllib3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("app.logging").info("Logging setup complete: level=%s, log_dir=%s", logging.getLevelName(level), log_dir)
