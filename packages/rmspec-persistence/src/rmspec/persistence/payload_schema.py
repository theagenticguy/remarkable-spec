"""The payload's own version gate, because ``user_version`` does not cover it.

``PRAGMA user_version`` tracks the DDL. But the real shape of a row is the
pydantic model whose JSON sits in its payload column, and that shape can change
without a single column moving: add a required field to
:class:`~rmspec.domain.models.SyncedDocument` and every stored row stops
validating while ``user_version`` still matches. The user then gets
``StoredRecordUnreadableError`` on every read, with no migration available and no
version skew to point at -- which is the defect this package exists to remove,
reborn in JSON.

So the field shape of every stored model is fingerprinted and pinned here. A
change to any of them fails :data:`PAYLOAD_FINGERPRINTS`' agreement test, which is
the prompt to decide *which kind* of change it is, and there are two:

* A field that **stored rows cannot satisfy** -- a new required field, a narrowed
  annotation, a field removed while a reader still needs it. Every stored row stops
  validating, so this needs a ``SCHEMA_VERSION`` bump and the migration that
  rewrites the affected payloads before the fingerprint is re-pinned.
* A field that **stored rows already satisfy**: a new optional field with a default.
  Nothing to rewrite -- the default *is* what an existing row means -- so the
  fingerprint is re-pinned on its own, and the reviewed record is the field's own
  docstring saying why its default is the truth about an older row rather than a
  placeholder. :attr:`~rmspec.domain.models.OcrArtifact.provenance` is the worked
  example.

Neither kind protects a *downgrade*. Every model here is ``extra="forbid"``, so a
row written by a newer build fails validation in an older one as
:class:`~rmspec.domain.errors.StoredRecordUnreadableError` whichever kind of change
produced it. That is stated on the models that carry it rather than smoothed over
with ``extra="ignore"``, which would buy downgrade safety by silently discarding a
component of a paid result.

The fingerprint is computed from ``model_fields`` -- each field's name, its
annotation, whether it is required -- expanded recursively through nested models,
rather than from ``model_json_schema()``. A JSON-schema hash would also change
when pydantic changes how it renders a schema, which would fail the gate on a
dependency bump that cannot possibly have broken a stored row.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, get_args

from pydantic import BaseModel

from rmspec.domain.models import (
    DiagramArtifact,
    DiagramCacheKey,
    OcrArtifact,
    OcrCacheKey,
    PageText,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "PAYLOAD_FINGERPRINTS",
    "STORED_MODELS",
    "payload_fingerprint",
]

#: Every payload column in the baseline schema and the model it holds. Keyed by
#: ``table.column`` so the agreement test can name the column it is checking.
STORED_MODELS: Final[Mapping[str, type[BaseModel]]] = {
    "document.payload": SyncedDocument,
    "page.payload": SyncedPage,
    "page_text.payload": PageText,
    "ocr_cache.key_payload": OcrCacheKey,
    "ocr_cache.artifact_payload": OcrArtifact,
    "diagram_cache.key_payload": DiagramCacheKey,
    "diagram_cache.artifact_payload": DiagramArtifact,
    "sync_audit.payload": SyncAuditEntry,
}


def _nested_models(annotation: object, /) -> Iterator[type[BaseModel]]:
    """Yield every model type reachable from one field annotation.

    Parameters
    ----------
    annotation
        A field's annotation, possibly a union, tuple or other generic.

    Yields
    ------
    type[BaseModel]
        Each nested model, so a change inside
        :class:`~rmspec.domain.models.TextProvenance` moves
        :class:`~rmspec.domain.models.PageText`'s fingerprint too.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for argument in get_args(annotation):
        yield from _nested_models(argument)


def _field_lines(model_type: type[BaseModel], /, *, seen: frozenset[str]) -> list[str]:
    """Return one canonical line per field, recursing into nested models.

    Parameters
    ----------
    model_type
        The model to describe.
    seen
        Model names already expanded, so a self-referential annotation
        terminates.

    Returns
    -------
    list[str]
        Lines in field declaration order, each naming the field, its annotation
        and whether it is required.
    """
    lines: list[str] = []
    for name, field in model_type.model_fields.items():
        lines.append(f"{name}|{field.annotation!s}|{field.is_required()}")
        for nested in _nested_models(field.annotation):
            if nested.__name__ in seen:
                continue
            lines.extend(
                f"{nested.__name__}.{line}"
                for line in _field_lines(nested, seen=seen | {nested.__name__})
            )
    return lines


def payload_fingerprint(model_type: type[BaseModel], /) -> str:
    """Return a stable fingerprint of one stored model's field shape.

    Parameters
    ----------
    model_type
        A model that occupies a payload column.

    Returns
    -------
    str
        Lowercase hex SHA-256 over the model's field names, annotations and
        required flags, in declaration order, expanded through nested models.
    """
    body = "\n".join(_field_lines(model_type, seen=frozenset({model_type.__name__})))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


#: Pinned fingerprints for schema version 1. Changing a stored model changes its
#: fingerprint, which fails the agreement test until this table, ``SCHEMA_VERSION``
#: and a migration move together.
PAYLOAD_FINGERPRINTS: Final[Mapping[str, str]] = {
    "diagram_cache.artifact_payload": (
        "204a176f4c3d267381acb05b88d3ab2ff560d953be77fa6eddf2998db4534958"
    ),
    "diagram_cache.key_payload": (
        "644bfc4635009d0701e3d033c2a705ddcf27284e32943ae85a9ebb0082888223"
    ),
    "document.payload": "e1b553d39062bc66d4692eea2453b7f77b2df2ce17a073d14f5ccb4ad9c7f00d",
    # Re-pinned when `OcrArtifact.provenance` landed. No `SCHEMA_VERSION` bump and no
    # migration: every field of `OcrProvenance` has a default, so every row written
    # before it still validates, and the defaults are what those rows mean -- they were
    # all tier-3 merges, because the merge was the only thing the writer would store.
    "ocr_cache.artifact_payload": (
        "9a607a9a1cb3353094e9b6f513e4a07bf43aeb1ef982d3b0c3ab0fbc7244067d"
    ),
    "ocr_cache.key_payload": "d34538d864efe40a012b627edfc2c000c82e8f44f518944057ee3a93362be15f",
    "page.payload": "8df2af581ca93f4d62bba716799e7f7796191635bd370d50d86c0041bc6b318b",
    "page_text.payload": "0f63268924d8af711c5e607fbfd2c64f5d82decadbad7388f42e4a2556dad26e",
    "sync_audit.payload": "f3c00cb173d78421abfdc9e2c45754dedb39f535be82d5ebd39f747e77033333",
}
