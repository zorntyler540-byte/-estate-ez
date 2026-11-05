"""Upload filenames that identify one photo across a whole sale.

Phone Sync names every still/clip once (``mobile-<draft>-NN.jpg``, ``append-<draft>-NN.jpg``,
``…-vid-NN.ext``) and the website names each staged photo ``web-<batch>-NNNN[-stem].ext``.
Those names are safe to dedupe on: the same name arriving again is the same photo (retry,
resume, Resend). Camera names (``IMG_1234.JPG``, iOS ``image.jpg``) repeat across devices,
counter rollovers and picker sessions, so they must never be treated as identity.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_PHONE_NAME = re.compile(
    r"^(?:mobile|append)-[0-9a-z][0-9a-z-]{6,}[0-9a-z]-(?:vid-)?\d+\.[a-z0-9]{2,5}$",
    re.IGNORECASE,
)
_WEB_NAME = re.compile(
    r"^web-[0-9a-f]{12,32}-\d{4,5}(?:-[a-z0-9_-]{1,60})?\.[a-z0-9]{2,5}$",
    re.IGNORECASE,
)


def upload_leaf_name(name: object | None) -> str:
    return PurePosixPath(str(name or "").replace("\\", "/")).name


def is_stable_upload_name(name: object | None) -> bool:
    """True for phone / website names that are unique per photo (see module docstring)."""
    leaf = upload_leaf_name(name)
    if not leaf:
        return False
    return bool(_PHONE_NAME.match(leaf) or _WEB_NAME.match(leaf))


_DIGITS = re.compile(r"(\d+)")


def natural_path_key(rel: str) -> tuple:
    """Folder-then-file sort with numbers compared by value (``IMG_9`` before ``IMG_10``).

    Stable names zero-pad their pick-order number, so they sort by pick order either way;
    this keeps camera-named folder jobs in shot order too.
    """
    parts = PurePosixPath(rel.replace("\\", "/").lower()).parts
    return tuple(
        tuple((0, int(tok), "") if tok.isdigit() else (1, 0, tok) for tok in _DIGITS.split(part) if tok)
        for part in parts
    )
