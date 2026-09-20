import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

class DataLoader:
    """统一的数据加载器，支持多组学数据格式"""
    
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.supported_formats = {
            'csv': self._load_csv,
            'tsv': self._load_tsv,
            'fastq': self._load_fastq,
            'vcf': self._load_vcf,
            'fasta': self._load_fasta,
        }
    
    def load_csv(self, file_path: str | Path) -> pd.DataFrame:
        """加载 CSV 文件"""
        return self._load_csv(file_path)
    
    def _load_csv(self, file_path: str | Path) -> pd.DataFrame:
        """内部 CSV 加载方法"""
        try:
            df = pd.read_csv(file_path)
            logger.info(f"Loaded CSV file: {file_path}, shape: {df.shape}")
            return df
        except Exception as e:
            logger.error(f"Failed to load CSV file {file_path}: {e}")
            raise
    
    def _load_tsv(self, file_path: str | Path) -> pd.DataFrame:
        """加载 TSV 文件"""
        return pd.read_csv(file_path, sep='\t')
    
    def _load_fastq(self, file_path: str | Path) -> dict[str, Any]:
        """加载 FASTQ 文件，返回记录数等信息"""
        try:
            record_count = 0
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('@'):
                        record_count += 1
            logger.info(f"Loaded FASTQ file: {file_path}, records: {record_count}")
            return {"format": "fastq", "file": str(file_path), "records": record_count}
        except Exception as e:
            logger.error(f"Failed to load FASTQ file {file_path}: {e}")
            raise
    
    def _load_vcf(self, file_path: str | Path) -> dict[str, Any]:
        """加载 VCF 文件，返回变异数等信息"""
        try:
            variant_count = 0
            header_lines = 0
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('#'):
                        header_lines += 1
                    else:
                        variant_count += 1
            logger.info(f"Loaded VCF file: {file_path}, variants: {variant_count}")
            return {"format": "vcf", "file": str(file_path), "variants": variant_count, "header_lines": header_lines}
        except Exception as e:
            logger.error(f"Failed to load VCF file {file_path}: {e}")
            raise
    
    def _load_fasta(self, file_path: str | Path) -> dict[str, Any]:
        """加载 FASTA 文件，返回序列数等信息"""
        try:
            sequence_count = 0
            total_length = 0
            current_length = 0
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('>'):
                        sequence_count += 1
                        if current_length > 0:
                            total_length += current_length
                            current_length = 0
                    else:
                        current_length += len(line.strip())
                if current_length > 0:
                    total_length += current_length
            logger.info(f"Loaded FASTA file: {file_path}, sequences: {sequence_count}, total_length: {total_length}")
            return {"format": "fasta", "file": str(file_path), "sequences": sequence_count, "total_length": total_length}
        except Exception as e:
            logger.error(f"Failed to load FASTA file {file_path}: {e}")
            raise
    
    def auto_detect_format(self, file_path: str | Path) -> str:
        """自动检测文件格式"""
        suffix = Path(file_path).suffix.lower()
        format_map = {
            '.csv': 'csv',
            '.tsv': 'tsv',
            '.fastq': 'fastq',
            '.fq': 'fastq',
            '.vcf': 'vcf',
            '.fasta': 'fasta',
            '.fa': 'fasta',
        }
        return format_map.get(suffix, 'unknown')