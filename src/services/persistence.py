"""Explicit local-storage and operator-accepted checkpoint RPO policy."""
import os
from pathlib import Path


def persistence_state(environ=None, *, backup=None, checks=None):
    env = os.environ if environ is None else environ
    root = env.get('BRAINAPI_DURABLE_ROOT', '/data')
    durable = Path(root).is_dir()
    mode = 'durable' if durable else 'ephemeral'
    configured = env.get('PERSISTENCE_MODE')
    if configured and configured != mode:
        raise ValueError('PERSISTENCE_MODE disagrees with the available durable root')
    backup, checks = backup or {}, checks or {}
    try:
        rpo = int(env.get('BRAIN_BACKUP_MAX_RPO_SECONDS', env.get('BRAIN_BACKUP_INTERVAL_SECONDS', '300')))
        valid_rpo = rpo > 0
    except ValueError:
        rpo, valid_rpo = None, False
    age = backup.get('backup_age_seconds')
    remote = checks.get('remote_checkpoint', backup.get('remote_checkpoint'))
    remote_ok = checks.get('remote_reachable', backup.get('remote_verified', False))
    checkpoint_ok = (backup.get('enabled') and backup.get('restore_qualified') and backup.get('state') == 'ready'
        and not backup.get('error') and remote_ok and bool(remote) and remote == backup.get('commit')
        and valid_rpo and isinstance(age, (int, float)) and 0 <= age <= rpo)
    dependencies = all(checks.get(k) is True for k in ('auth', 'database', 'embedding_profile', 'access_logging'))
    accepted = env.get('ALLOW_CHECKPOINTED_PRODUCTION', 'false').lower() == 'true'
    local_ok = durable and (not backup.get('enabled') or checkpoint_ok)
    production = bool((local_ok or (accepted and checkpoint_ok)) and dependencies)
    # Preserve the previous local-only storage query used during early boot.
    if not backup and not checks:
        production = durable
    return {'mode': mode, 'root': str(Path(root) / 'brainapi') if durable else '/app/.data/brainapi',
            'production_ready': production, 'local': mode,
            'content_recovery': 'github-checkpoint' if backup.get('enabled') else ('local' if durable else 'none'),
            'content_durable': bool(checkpoint_ok or durable), 'zero_loss': False,
            'zero_loss_durable': False, 'rpo_seconds': rpo,
            'checkpointed_production_accepted': accepted}


def enforce_persistence():
    state = persistence_state()
    if (state['mode'] == 'ephemeral' and os.getenv('ENV', 'production') != 'development'
            and os.getenv('ALLOW_EPHEMERAL_STORAGE', 'false').lower() != 'true'
            and os.getenv('ALLOW_CHECKPOINTED_PRODUCTION', 'false').lower() != 'true'):
        raise RuntimeError('EPHEMERAL_STORAGE_REQUIRES_OVERRIDE: configure durable storage or explicitly accept checkpoint RPO/staging')
    return state
