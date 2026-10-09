"""Validated startup limits for file and attachment resources."""
import os


def integer(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default))
    if not raw.isdecimal() or not minimum <= int(raw) <= maximum:
        raise ValueError(f'{name} must be {minimum} to {maximum}')
    return int(raw)


FILE_MAX = integer('MANAGER_FILE_MAX_BYTES', 20 * 1024**2, 1, 20 * 1024**2)
FILES_PER_KEY = integer('MANAGER_FILES_PER_KEY', 128, 1, 1024)
OWNER_BYTES = integer('MANAGER_FILES_OWNER_BYTES', 256 * 1024**2, FILE_MAX, 1024**3)
TOTAL_BYTES = integer('MANAGER_FILES_TOTAL_BYTES', 1024**3, OWNER_BYTES, 8 * 1024**3)
DEFAULT_TTL = integer('MANAGER_FILE_DEFAULT_TTL_SECONDS', 0, 0, 7776000)
if DEFAULT_TTL and DEFAULT_TTL < 3600:
    raise ValueError('MANAGER_FILE_DEFAULT_TTL_SECONDS must be 0 or at least 3600')
TOOL_REQUEST_MAX = integer('MANAGER_TOOL_REQUEST_MAX_BYTES', 32 * 1024**2, 131072, 32 * 1024**2)
ATTACHMENT_COUNT = integer('MANAGER_ATTACHMENT_MAX_COUNT', 16, 1, 16)
MEDIA_INFLIGHT = integer('MANAGER_MEDIA_MAX_INFLIGHT', 2, 1, 4)
