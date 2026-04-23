import hashlib
from typing import Optional

import jcs


def canonical_json(obj: dict) -> bytes:
    """Serialize a dict to RFC 8785 canonical JSON bytes."""
    return jcs.canonicalize(obj)


def row_hash(obj: dict) -> str:
    """SHA-256 hash of the canonical JSON representation of a row."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def query_fingerprint(conv: dict) -> Optional[str]:
    """Hash of all user-role message contents, for detecting response refreshes.

    Returns None if there are no user messages.
    """
    messages = conv.get("messages", [])
    user_contents = [
        m["content"] for m in messages
        if m.get("role") == "user" and "content" in m
    ]
    if not user_contents:
        return None
    combined = "\n".join(user_contents)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
