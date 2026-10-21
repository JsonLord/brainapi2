"""A rejected or malformed ingestion response must never start task polling."""
def accepted_task_id(status, payload):
    task_id = payload.get('task_id') if isinstance(payload, dict) else None
    if status != 202 or not isinstance(task_id, str) or task_id.strip().lower() in {'', 'none', 'null'}:
        raise ValueError('INGESTION_CONTRACT_ERROR: expected HTTP 202 with a non-empty task_id')
    return task_id
