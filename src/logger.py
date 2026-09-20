import logging
import sys
from pathlib import Path
from typing import Optional


class LoggerFactory:
    """日志工厂类"""

    @staticmethod
    def setup_logger(
        name: str,
        log_file: Optional[str] = None,
        level: int = logging.INFO,
        format: str = None
    ) -> logging.Logger:
        """设置日志记录器"""

        if format is None:
            format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        logger = logging.getLogger(name)
        logger.setLevel(level)

        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(format)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # 文件处理器（如果指定）
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(exist_ok=True)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(format)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

        return logger

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """获取现有日志记录器"""
        return logging.getLogger(name)


# 设置根日志记录器
def setup_root_logger(log_file: str = "logs/multiomics_agent.log"):
    """设置根日志记录器"""
    return LoggerFactory.setup_logger(
        name="multiomics_agent",
        log_file=log_file,
        level=logging.INFO
    )
