import sys
sys.path.insert(0, r'D:\Project\Multiomics-Agent')

from src.control.intent_parser import IntentParser

parser = IntentParser()

test_cases = [
    '与致癌相关的基因有什么',
    'TP53 是什么基因',
    '哪些基因与癌症相关',
    '帮我下载 GSE123456 数据集',
    '画一个火山图',
    'TP53 的作用是什么',
    '分析差异表达基因',
]

print('=== 意图解析测试 ===')
for inp in test_cases:
    intent = parser.parse(inp)
    params = parser.extract_parameters(inp)
    print(f'输入: {inp}')
    print(f'  意图: {intent}')
    print(f'  参数: {params}')
    print()