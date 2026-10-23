"""Cross-process admission, accepted-work drain and explicit durable handoff.

Only IDs/states are kept locally; this is not an auth/session registry. The hold
is also carried in the verified manifest so replacement containers require resume.
"""
import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from .git import BackupError
from .state import enabled, locked, state_root, write_status, public_status

_admitted = ContextVar('backup_admitted', default=False)


def _read(name, default):
    path = state_root() / name
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        raise BackupError('BACKUP_MAINTENANCE_STATE_INVALID') from None


def _write(name, value):
    path = state_root() / name
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, sort_keys=True))
    temp.replace(path)


def status():
    if not enabled():
        return {'state': 'disabled', 'quiesced': False, 'pending_tasks': 0}
    with locked(state_root() / 'control.lock'):
        control = _read('maintenance.json', {'state': 'active', 'quiesced': False})
        tasks = _read('work.json', {})
        return {**control, 'pending_tasks': sum(v == 'pending' for v in tasks.values())}


def initialize(hold=None, preserve_work=False):
    with locked(state_root() / 'control.lock'):
        if not preserve_work:
            _write('work.json', {})
        prior = _read('maintenance.json', {}) if preserve_work else {}
        if prior.get('quiesced'):
            return
        _write('maintenance.json', {'state': 'quiesced' if hold else 'active',
            'quiesced': bool(hold), 'sealed': bool(hold), 'handoff': hold or {}})


def hold_manifest():
    value = status()
    return value.get('handoff', {}) if value.get('quiesced') else {}


@contextmanager
def admission():
    """Hold a shared lease until an admitted mutation request has finished."""
    if not enabled() or _admitted.get():
        yield
        return
    path = state_root() / 'admission.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lease:
        if os.fstat(lease.fileno()).st_uid == os.geteuid():
            os.fchmod(lease.fileno(), 0o660)
        try:
            fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('BACKUP_MAINTENANCE_QUIESCED') from None
        try:
            with locked(state_root() / 'control.lock'):
                if _read('maintenance.json', {}).get('quiesced'):
                    raise BackupError('BACKUP_MAINTENANCE_QUIESCED')
            token = _admitted.set(True)
            try:
                yield
            finally:
                _admitted.reset(token)
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def register_task(task_id, parent=None):
    if not enabled():
        return
    with locked(state_root() / 'control.lock'):
        control = _read('maintenance.json', {})
        tasks = _read('work.json', {})
        accepted_parent = parent is not None and tasks.get(parent) == 'pending'
        if control.get('sealed') or (control.get('quiesced') and not (_admitted.get() or accepted_parent)):
            raise BackupError('BACKUP_MAINTENANCE_QUIESCED')
        tasks[task_id] = 'pending'
        _write('work.json', tasks)


def begin_task(task_id):
    """Count running deliveries separately, including broker redeliveries."""
    with locked(state_root() / 'control.lock'):
        tasks = _read('work.json', {})
        if not task_id or tasks.get(task_id) != 'pending':
            raise BackupError('BACKUP_UNREGISTERED_TASK')
        execution = uuid.uuid4().hex
        tasks[execution] = 'pending'
        _write('work.json', tasks)
        return execution


def finish_task(task_id, execution=None):
    if enabled():
        with locked(state_root() / 'control.lock'):
            tasks = _read('work.json', {})
            tasks.pop(task_id, None)
            tasks.pop(execution, None)
            _write('work.json', tasks)


def quiesce():
    if not enabled():
        raise BackupError('BACKUP_DISABLED')
    with locked(state_root() / 'control.lock'):
        control = _read('maintenance.json', {})
        if not control.get('quiesced'):
            control = {'state': 'draining', 'quiesced': True, 'sealed': False,
                       'handoff': {'id': uuid.uuid4().hex, 'quiesced': True}}
            _write('maintenance.json', control)
    return status()


def fail(code):
    quiesce()
    with locked(state_root() / 'control.lock'):
        control = _read('maintenance.json', {})
        control.update(state='degraded', error=code)
        _write('maintenance.json', control)
    write_status(state='degraded', error=code)


def _seal(timeout):
    deadline = time.monotonic() + timeout
    path = state_root() / 'admission.lock'
    with path.open('a+') as lease:
        while True:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BackupError('BACKUP_DRAIN_TIMEOUT')
                time.sleep(.05)
        try:
            while True:
                with locked(state_root() / 'control.lock'):
                    tasks = _read('work.json', {})
                    if not any(v == 'pending' for v in tasks.values()):
                        control = _read('maintenance.json', {})
                        control.update(state='quiesced', sealed=True)
                        _write('maintenance.json', control)
                        return
                if time.monotonic() >= deadline:
                    raise BackupError('BACKUP_DRAIN_TIMEOUT')
                time.sleep(.05)
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def checkpoint(manager, timeout=120):
    with locked(state_root() / 'barrier.lock'):
        quiesce()
        try:
            _seal(timeout)
            head, _ = manager.backup()
            manager.verify_checkpoint(head)
            if public_status().get('backup_dirty'):
                raise BackupError('BACKUP_DRAIN_WRITES_DETECTED')
            with locked(state_root() / 'control.lock'):
                control = _read('maintenance.json', {})
                control.update(state='durable', checkpoint=head, remote_verified=True)
                control.pop('error', None)
                _write('maintenance.json', control)
            return {'state': 'durable', 'checkpoint': head, 'dirty': False,
                    'remote_verified': True, 'quiesced': True}
        except Exception as exc:
            code = str(exc) if isinstance(exc, BackupError) else 'BACKUP_BARRIER_FAILED'
            fail(code)
            raise BackupError(code) from None


def resume(manager):
    with locked(state_root() / 'barrier.lock'):
        if not status().get('quiesced'):
            return status()
        try:
            _seal(0)
            # Publish release while local admission is still closed. Failure leaves
            # both this container and replacements safely held.
            head, _ = manager.backup(maintenance={})
            manager.verify_checkpoint(head)
            initialize()
            return {'state': 'active', 'quiesced': False, 'checkpoint': head}
        except Exception as exc:
            code = str(exc) if isinstance(exc, BackupError) else 'BACKUP_RESUME_FAILED'
            fail(code)
            raise BackupError(code) from None
