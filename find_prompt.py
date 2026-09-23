with open('D:\\Project\\Multiomics-Agent\\pubmed-etl\\cleaner\\llm_validator.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find SYSTEM_PROMPT start
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'SYSTEM_PROMPT = (' in line:
        start_idx = i
        break

if start_idx is not None:
    # Find the closing ) on a line by itself
    paren_count = 0
    for i in range(start_idx, len(lines)):
        line = lines[i]
        paren_count += line.count('(') - line.count(')')
        if paren_count == 0 and i > start_idx:
            end_idx = i
            break
    
    print(f"Found SYSTEM_PROMPT at lines {start_idx} to {end_idx}")
    block = ''.join(lines[start_idx:end_idx+1])
    print("Current block preview:")
    print(block[:200])
    print("...")
    print(block[-200:])
else:
    print("NOT FOUND")