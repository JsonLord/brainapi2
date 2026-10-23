"""Shared redaction for trace payloads, exceptions, and application log records."""

import logging
import os
import re
import traceback
from urllib.parse import unquote

_SECRET_NAMES = {"SYSTEM_PAT", "GITHUB_PAT", "HF_TOKEN", "BRAINPAT_TOKEN", "POSTGRES_PASSWORD", "REDIS_PASSWORD"}
_SECRET_KEYS = {"github_pat", "hf_token", "authorization", "brainpat", "token", "token_hash", "password", "system_pat", "brainpat_token", "pat", "access_token", "api_key", "csrf_token", "client_secret"}


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if str(key).lower() in _SECRET_KEYS else redact(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    # Access paths may contain percent-encoded query keys or credential values.
    for _ in range(2):
        value = unquote(value, errors="replace")
    for name in _SECRET_NAMES:
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\b(authorization|brainpat|system_pat|brainpat_token|github_pat|hf_token|password|pat|token|access_token|api_key|csrf_token|client_secret)\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|(?:Bearer\s+)?[^\s,;&?#]+)", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)(/(?:tokens?|brainpat|password|secrets?)/)[^/?#\s]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bbpat_[A-Za-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"\b(?:ghp_|github_pat_|hf_)[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
    value = re.sub(r"\b[a-z0-9]{48}\b", "[REDACTED]", value)
    return re.sub(r"(\w+://)[^\s/@]*:[^\s/@]+@", r"\1[REDACTED]@", value)


def install_log_redaction():
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_brainapi_redaction", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        # Uvicorn's AccessFormatter unpacks the original five positional args.
        # Redact components before interpolation without flattening the record.
        rendered = record.getMessage()
        safe_rendered = redact(rendered)
        def component(value):
            safe = redact(value)
            if isinstance(value, str) and safe == value and value and value in rendered and value not in safe_rendered:
                return "[REDACTED]"
            return safe
        if isinstance(record.args, tuple):
            record.args = tuple(component(v) for v in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: "[REDACTED]" if str(k).lower() in _SECRET_KEYS else component(v)
                           for k, v in record.args.items()}
        if record.args and isinstance(record.msg, str):
            # Redact literal template segments, preserving every %-conversion.
            conversions = r"(%(?:\([^)]+\))?[-+#0-9. *]*[diouxXeEfFgGcrsa%])"
            record.msg = "".join(part if i % 2 else redact(part)
                                 for i, part in enumerate(re.split(conversions, record.msg)))
        else:
            record.msg = redact(record.msg)
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            if isinstance(target, str):
                path, separator, _ = target.partition("?")
                target = path + ("?[REDACTED]" if separator else "")
            record.args = address, method, target, version, status
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return record

    factory._brainapi_redaction = True
    logging.setLogRecordFactory(factory)


def access_logging_healthy():
    """Exercise the installed factory and real Uvicorn formatter without emitting."""
    from uvicorn.logging import AccessFormatter
    try:
        record = logging.getLogRecordFactory()("uvicorn.access", logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", "GET", "/health?token=access-log-self-test", "1.1", 200), None)
        formatted = AccessFormatter('{client_addr} - "{request_line}" {status_code}', style="{", use_colors=False).format(record)
        return "access-log-self-test" not in formatted and "GET /health" in formatted and "200" in formatted
    except Exception:
        return False
