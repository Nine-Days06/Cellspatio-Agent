with open('D:\\Project\\Multiomics-Agent\\pubmed-etl\\cleaner\\llm_validator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update PROMPT_PREFIX
old_prefix = 'PROMPT_PREFIX = (\n    "请根据上述标准判断以下文献是否包含可用于人类多组学知识图谱构建的实体关系信息。\\n\\n"\n)'
new_prefix = 'PROMPT_PREFIX = (\n    "请根据上述标准判断以下文献是否包含可用于人类单细胞与空间组学知识图谱构建的实体关系信息。\\n\\n"\n)'

if old_prefix in content:
    content = content.replace(old_prefix, new_prefix)
    print('Updated PROMPT_PREFIX')
else:
    print('PROMPT_PREFIX old not found')

# Update project name in metadata
old_project = '"project": "multiomics-literature-selection"'
new_project = '"project": "cellspatio-literature-selection"'
if old_project in content:
    content = content.replace(old_project, new_project)
    print('Updated project name')
else:
    print('Project name not found')

# Update export comment
old_export = '导出主项目（multiomics-agent）兼容 CSV'
new_export = '导出主项目（cellspatio-agent）兼容 CSV'
if old_export in content:
    content = content.replace(old_export, new_export)
    print('Updated export comment')
else:
    print('Export comment not found')

with open('D:\\Project\\Multiomics-Agent\\pubmed-etl\\cleaner\\llm_validator.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')