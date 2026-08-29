r"""The one digesting primitive the whole domain shares, and why it is length-prefixed.

Every cache key in this workspace is a SHA-256 over an ordered set of components. Five
modules used to fold those components together themselves, each with its own
``_FIELD_SEPARATOR = b"\x1f"`` and its own ``hasher.update`` sequence, and three of the
five were reachably ambiguous: a separator is only unambiguous while no component can
contain it, and several components are open strings an adapter authors.

The defect, concretely
----------------------
``VisionRequest.digest`` hashed ``system``, then a separator, then ``prompt``. So::

    system="a",       prompt="b\x1fc"
    system="a\x1fb",  prompt="c"

produced identical bytes and therefore one cache key for two different requests. Prompts
embed arbitrary recognizer output, so this is reachable rather than theoretical: one page's
transcription can be served for another's request. The same shape was live in
``OcrCacheKey.digest`` (``model_fingerprint`` is contractually opaque and adapter-authored,
and the recognizer slugs were joined on ``\x1e`` by the caller) and in
``PageBackground.digest`` (``template_svg`` is arbitrary markup).

The fix, and why it is a primitive rather than a rule
-----------------------------------------------------
:func:`digest_of` prefixes every component with its own length, so the byte stream can be
parsed back into exactly one component list and no two distinct lists can produce the same
stream. It also folds in the *number* of components, which is what makes a variable-length
component list -- image digests, recognizer slugs -- safe without each call site
remembering to hash a count first.

It is a function, not a documented convention, because a convention is re-implemented once
per module and was: two ``RasterImage.digest`` bodies in different ports modules had to stay
byte-identical forever, on trust, or identical pixels would hash to two keys. They now call
this with the same arguments, so agreement is mechanical and the residual risk -- a
divergent argument list -- is what ``test_ports_ocr``'s cross-twin equality test covers.

Tags and versions
-----------------
The ``tag`` is a domain-and-version label. Two key types with identical components cannot
collide because their tags differ, and a change to *how* a digest is composed is a
mechanical cache miss -- every stored row keyed on the old scheme becomes unreachable
rather than being silently reinterpreted. Every tag in the domain moved from ``v1`` to
``v2`` when framing landed, because the byte stream changed for every one of them.

Notes
-----
This module is deliberately not a ports module and imports only ``hashlib``, so both
:mod:`rmspec.domain.models` and every ``rmspec.domain.ports`` module may import it without
a ports module importing a sibling.
"""

from __future__ import annotations

import hashlib
from typing import Final

__all__ = ["LENGTH_PREFIX_BYTES", "digest_of", "framed"]

LENGTH_PREFIX_BYTES: Final = 8
"""Width of the big-endian length that prefixes every framed component.

Eight bytes rather than a varint: fixed width keeps :func:`framed` total for any input a
process can hold, and the four bytes saved per component are worth nothing against a
scheme whose whole job is to be unambiguous.
"""


def framed(part: bytes, /) -> bytes:
    """Return ``part`` prefixed by its own length, so concatenation stays parseable.

    Parameters
    ----------
    part
        The component's bytes. May be empty; an empty component is a length of zero
        followed by nothing, which is distinct from the component being absent because the
        count of components is folded in separately by :func:`digest_of`.

    Returns
    -------
    bytes
        ``len(part)`` as eight big-endian bytes, followed by ``part``.
    """
    return len(part).to_bytes(LENGTH_PREFIX_BYTES, "big") + part


def digest_of(tag: bytes, /, *parts: bytes) -> str:
    """Fold a domain tag and an ordered component list into one unambiguous hex digest.

    The stream is ``framed(tag)``, then ``framed`` of the component count, then ``framed``
    of each component in order. Two distinct ``(tag, parts)`` pairs cannot produce the same
    stream, so two distinct inputs cannot share a cache key.

    Parameters
    ----------
    tag
        Domain-and-version label, such as ``b"rmspec.cache.ocr.v2"``. Bump the version
        whenever the component list or its order changes, so old rows miss instead of being
        read as if they described the new scheme.
    parts
        The components, in a fixed order. Encode text with ``.encode()`` at the call site
        rather than passing ``str``: the caller owns the encoding, and a component that is
        already bytes -- image data -- must not be re-encoded.

    Returns
    -------
    str
        Lowercase hex SHA-256.
    """
    hasher = hashlib.sha256()
    hasher.update(framed(tag))
    hasher.update(framed(len(parts).to_bytes(LENGTH_PREFIX_BYTES, "big")))
    for part in parts:
        hasher.update(framed(part))
    return hasher.hexdigest()
