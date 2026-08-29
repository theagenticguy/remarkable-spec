"""The page fingerprint, kept as its own module because it is a compatibility surface.

``DocumentRepository.page_fingerprint`` is documented to return an opaque token, so
any stable digest would satisfy the port. This one is not free to change anyway: it
is the legacy ``sync/hasher.py`` ``rm_hash``, the value already persisted in
``~/.remarkable-spec/sync.db`` as ``SyncedPage.rm_hash`` and folded into
``OcrCacheKey.page_hash`` / ``DiagramCacheKey.page_hash`` by every cached OCR and
diagram row written so far.

Two consequences, recorded here so neither is rediscovered by breaking a cache:

1. **Unsalted, and never routed through the domain's tagged ``_digest``.** That helper
   prefixes a domain-and-version tag before hashing, which is right for a composite
   cache key and wrong here: adding a tag changes every digest at once, so every
   existing cache row misses and every paid pipeline re-runs.
2. **A zero-byte artifact hashes normally**, to
   ``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855``. That is a
   legitimate value and is deliberately *not* folded into
   ``ABSENT_ARTIFACT_FINGERPRINT``: the empty stub is a file that exists, and keeping
   it on the content-hash path means the key changes the instant ink is added, while
   the sentinel is defined as staying valid for as long as there is no artifact at all.
"""

from __future__ import annotations

import hashlib

__all__ = ["fingerprint_bytes"]


def fingerprint_bytes(raw: bytes, /) -> str:
    """Fingerprint one artifact's stored bytes.

    Parameters
    ----------
    raw
        The artifact's bytes exactly as read, before any decoding.

    Returns
    -------
    str
        Lowercase hex SHA-256, 64 characters. Distinct from
        ``ABSENT_ARTIFACT_FINGERPRINT`` for every possible input, since that
        sentinel is not hex-shaped.
    """
    return hashlib.sha256(raw).hexdigest()
