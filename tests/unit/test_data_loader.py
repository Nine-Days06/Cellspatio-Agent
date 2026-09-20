import os
import tempfile
from pathlib import Path

import pandas as pd


def test_load_csv_data():
    """Test loading CSV data files"""
    # 创建临时 CSV 文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("gene,sample1,sample2,condition\n")
        f.write("TP53,10.2,11.5,treatment\n")
        f.write("BRCA1,8.7,9.2,control\n")
        temp_path = f.name
    
    try:
        from src.data.data_loader import DataLoader
        loader = DataLoader()
        data = loader.load_csv(temp_path)
        
        assert isinstance(data, pd.DataFrame)
        assert data.shape == (2, 4)
        assert 'gene' in data.columns
    finally:
        os.unlink(temp_path)

def test_load_fastq_file():
    """Test loading FASTQ format files"""
    # 创建临时 FASTQ 文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.fastq', delete=False) as f:
        f.write("@read1\n")
        f.write("ATCGATCG\n")
        f.write("+\n")
        f.write("IIIIIIII\n")
        f.write("@read2\n")
        f.write("GCTAGCTA\n")
        f.write("+\n")
        f.write("IIIIIIII\n")
        temp_path = f.name
    
    try:
        from src.data.data_loader import DataLoader
        loader = DataLoader()
        result = loader._load_fastq(temp_path)
        
        assert isinstance(result, dict)
        assert result['format'] == 'fastq'
        assert result['records'] == 2
        assert result['file'] == temp_path
    finally:
        os.unlink(temp_path)

def test_auto_detect_format():
    """Test automatic format detection"""
    from src.data.data_loader import DataLoader
    loader = DataLoader()
    
    # 测试各种扩展名
    test_cases = [
        ('data.csv', 'csv'),
        ('data.tsv', 'tsv'),
        ('data.fastq', 'fastq'),
        ('data.fq', 'fastq'),
        ('data.vcf', 'vcf'),
        ('data.fasta', 'fasta'),
        ('data.fa', 'fasta'),
        ('data.txt', 'unknown'),
        ('data.xyz', 'unknown'),
    ]
    
    for filename, expected_format in test_cases:
        # 创建临时文件路径（不需要实际文件，只测试扩展名检测）
        temp_path = Path(tempfile.gettempdir()) / filename
        detected = loader.auto_detect_format(temp_path)
        assert detected == expected_format, f"Failed for {filename}: expected {expected_format}, got {detected}"