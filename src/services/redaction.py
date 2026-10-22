"""Shared redaction for trace payloads, exceptions, and application log records."""

import logging
import os
import re
import traceback

_SECRET_NAMES = {"SYSTEM_PAT", "GITHUB_PAT", "HF_TOKEN", "BRAINPAT_TOKEN", "POSTGRES_PASSWORD", "REDIS_PASSWORD"}
_SECRET_KEYS = {"github_pat", "hf_token", "authorization", "brainpat", "token", "token_hash", "password", "system_pat", "brainpat_token", "pat"}


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if str(key).lower() in _SECRET_KEYS else redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    for name in _SECRET_NAMES:
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\b(authorization|brainpat|system_pat|brainpat_token|password|pat)\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|(?:Bearer\s+)?[^\s,;]+)", r"\1=[REDACTED]", value)
    value = re.sub(r"\bbpat_[A-Za-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"\b[a-z0-9]{48}\b", "[REDACTED]", value)
    return re.sub(r"(\w+://)[^\s/@]*:[^\s/@]+@", r"\1[REDACTED]@", value)


def install_log_redaction():
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_brainapi_redaction", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return record

    factory._brainapi_redaction = True
    logging.setLogRecordFactory(factory)
