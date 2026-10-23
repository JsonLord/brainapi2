"""Account for every accepted Celery delivery, including retries and chains."""
import uuid
from celery import Task, current_task
from . import maintenance as gate
from .state import enabled
from .git import BackupError

HEADER = 'brainapi-backup-work'


class ContentTask(Task):
    abstract = True

    def apply_async(self, args=None, kwargs=None, **options):
        if not enabled():
            return super().apply_async(args=args, kwargs=kwargs, **options)
        parent = getattr(getattr(current_task, 'request', None), 'headers', None) or {}
        work_id = uuid.uuid4().hex
        parent_id = getattr(getattr(current_task, 'request', None), 'backup_execution', None) or parent.get(HEADER)
        gate.register_task(work_id, parent_id)
        headers = dict(options.pop('headers', None) or {})
        headers[HEADER] = work_id
        # An ambiguous publication failure deliberately stays pending: fail closed
        # rather than claim a durable barrier while a message might be in flight.
        return super().apply_async(args=args, kwargs=kwargs, headers=headers, **options)

    def __call__(self, *args, **kwargs):
        if not enabled():
            return super().__call__(*args, **kwargs)
        work_id = (self.request.headers or {}).get(HEADER)
        self.request.backup_execution = gate.begin_task(work_id)
        token = gate._admitted.set(True)
        try:
            return super().__call__(*args, **kwargs)
        finally:
            gate._admitted.reset(token)

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        gate.finish_task((self.request.headers or {}).get(HEADER), getattr(self.request, 'backup_execution', None))
        return super().after_return(status, retval, task_id, args, kwargs, einfo)

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        gate.finish_task((self.request.headers or {}).get(HEADER), getattr(self.request, 'backup_execution', None))
        return super().on_retry(exc, task_id, args, kwargs, einfo)
