import ast
with open(r'D:\Project\Multiomics-Agent\src\control\workflow_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()
try:
    ast.parse(content)
    print('Syntax OK')
except SyntaxError as e:
    print(f'SyntaxError: {e.msg} at line {e.lineno}, offset {e.offset}')
    lines = content.split('\n')
    for i in range(max(0, e.lineno-5), min(len(lines), e.lineno+5)):
        print(f'{i+1:4d}: {lines[i]}')