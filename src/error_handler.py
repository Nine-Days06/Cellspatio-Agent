import logging
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


class ErrorHandler:
    """统一错误处理器"""

    @staticmethod
    def handle_llm_error(error: Exception, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """处理 LLM 调用错误"""
        error_msg = f"LLM 调用失败: {error!s}"
        logger.error(error_msg)

        return {
            'status': 'error',
            'error_type': 'llm_error',
            'message': error_msg,
            'suggestion': '请检查 API 密钥和网络连接，或尝试使用本地模型。',
            'context': context or {}
        }

    @staticmethod
    def handle_data_error(error: Exception, file_path: str | None = None) -> dict[str, Any]:
        """处理数据错误"""
        error_msg = f"数据处理错误: {error!s}"
        logger.error(error_msg)

        suggestion = "请检查文件格式是否正确，支持的格式包括 CSV、TSV、FASTQ、VCF。"
        if file_path:
            suggestion += f" 文件: {file_path}"

        return {
            'status': 'error',
            'error_type': 'data_error',
            'message': error_msg,
            'suggestion': suggestion,
        }

    @staticmethod
    def handle_analysis_error(error: Exception, analysis_type: str | None = None) -> dict[str, Any]:
        """处理分析错误"""
        error_msg = f"分析执行错误: {error!s}"
        logger.error(error_msg)

        suggestion = "分析过程中出现错误，请检查输入数据和参数。"
        if analysis_type:
            suggestion += f" 分析类型: {analysis_type}"

        return {
            'status': 'error',
            'error_type': 'analysis_error',
            'message': error_msg,
            'suggestion': suggestion,
        }

    @staticmethod
    def handle_knowledge_error(error: Exception, query: str | None = None) -> dict[str, Any]:
        """处理知识检索错误"""
        error_msg = f"知识检索错误: {error!s}"
        logger.error(error_msg)

        return {
            'status': 'error',
            'error_type': 'knowledge_error',
            'message': error_msg,
            'suggestion': '知识库可能未初始化或查询格式不正确。',
            'query': query,
        }


def safe_execute(func):
    """安全执行装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # 根据函数名推断错误类型
            func_name = func.__name__
            if 'llm' in func_name or 'model' in func_name:
                return ErrorHandler.handle_llm_error(e)
            elif 'data' in func_name or 'load' in func_name:
                return ErrorHandler.handle_data_error(e)
            elif 'analysis' in func_name or 'r_' in func_name:
                return ErrorHandler.handle_analysis_error(e)
            elif 'knowledge' in func_name or 'query' in func_name:
                return ErrorHandler.handle_knowledge_error(e)
            else:
                # 通用错误处理
                logger.exception("未处理的错误，请检查输入参数或联系支持。")
                return {
                    'status': 'error',
                    'message': f"执行错误: {e!s}",
                    'suggestion': '请检查输入参数或联系支持。'
                }
    return wrapper
