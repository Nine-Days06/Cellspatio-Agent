from src.schemas.workflow import WorkflowExecution
import json

schema = WorkflowExecution.model_json_schema()
defs = schema.get('$defs', {})
wfs = defs.get('WorkflowStep', {})
print('FOUND:', bool(wfs))
if wfs:
    print(json.dumps(wfs, indent=2, ensure_ascii=False))