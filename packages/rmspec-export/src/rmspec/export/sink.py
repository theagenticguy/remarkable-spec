"""Filesystem-backed :class:`~rmspec.domain.ports.export.ArtifactSink`."""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

from rmspec.domain.errors import ArtifactWriteFailed, ArtifactWriteReason
from rmspec.domain.ports.export import ArtifactMedia, ArtifactRef

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rmspec.domain.ports.export import ArtifactName

__all__ = ["FilesystemArtifactSink"]

SUFFIXES: Final[Mapping[ArtifactMedia, str]] = {
    ArtifactMedia.SVG: ".svg",
    ArtifactMedia.PNG: ".png",
    ArtifactMedia.PDF: ".pdf",
}
"""Exhaustive over :class:`ArtifactMedia`, which is the sole source of an artifact's suffix.

Exhaustive on purpose: a ``dict.get`` with a default would let a fourth media member land a
suffix-less file, and the enum's whole job is that a name and its content cannot disagree.
"""

TEMPORARY_PREFIX: Final = ".rmspec-"
"""Prefix of the in-flight temporary, deliberately a constant rather than the artifact's name.

Deriving it from ``name.value`` -- ``prefix=f".{name.value}-"`` -- imported a ceiling the
destination filesystem does not have. Measured on this machine, where
``os.pathconf(destination, "PC_NAME_MAX")`` is 255: a 241-character stem made the temporary
``"." + 241 + "-" + 8 random + ".part"`` = 256 characters, so
:func:`tempfile.NamedTemporaryFile` failed with ``ENAMETOOLONG`` before a single byte reached
a destination whose *target* name was a legal 245. The legacy exporters' plain
``output.write_bytes`` accepted that stem and every stem up to 251. The sink's own ceiling was
therefore ``NAME_MAX - 15``, an artefact of its prefix and suffix rather than of the
filesystem or of :class:`ArtifactName`, which imposes no length limit and is documented as
being derived from a document title.

With a constant prefix the temporary is a fixed 21 characters and the only surviving limit is
the real one: ``len(name.value) + len(SUFFIXES[media]) <= NAME_MAX``, which is what legacy
allowed. That equivalence is asserted at both boundaries in ``test_export_sink.py``.
"""

TEMPORARY_SUFFIX: Final = ".part"
"""Suffix of the in-flight temporary, so an orphan is recognisable as one."""

_ERRNO_REASONS: Final[Mapping[int, ArtifactWriteReason]] = {
    errno.EEXIST: ArtifactWriteReason.ALREADY_PRESENT,
    errno.EACCES: ArtifactWriteReason.NOT_WRITABLE,
    errno.EPERM: ArtifactWriteReason.NOT_WRITABLE,
    errno.EROFS: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENOTDIR: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENOENT: ArtifactWriteReason.NOT_WRITABLE,
    errno.EISDIR: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENAMETOOLONG: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENOSPC: ArtifactWriteReason.OUT_OF_SPACE,
    errno.EDQUOT: ArtifactWriteReason.OUT_OF_SPACE,
    errno.EFBIG: ArtifactWriteReason.OUT_OF_SPACE,
}
"""``OSError.errno`` to the domain's closed reason set.

``ENAMETOOLONG`` is mapped explicitly rather than left to fall through, and the mapping is
only honest because :data:`TEMPORARY_PREFIX` is a constant: the sole remaining way to reach
it is a *target* name the destination filesystem will not accept, which is exactly
``NOT_WRITABLE`` -- "the destination cannot be created". While the temporary's name was
derived from the artifact's, the same reason was reported for a destination that could be
created perfectly well, sending a caller to ``chmod`` or to another directory over a
name-construction bug it could not fix. Reporting either as ``INTERRUPTED`` would be a
different typed lie: nothing was interrupted and no partial file exists. Anything genuinely
unmapped means the write started and did not finish, which is what ``INTERRUPTED`` says.
"""


class FilesystemArtifactSink:
    r"""Commit export bytes into one directory, atomically, and name what landed.

    Satisfies :class:`~rmspec.domain.ports.export.ArtifactSink`. ``REQUEST`` scope: it holds
    the invocation's destination, overwrite policy and dry-run flag, which is why none of
    those appear in :meth:`write`.

    Consolidates behaviour the legacy exporters each did their own way
    ----------------------------------------------------------------
    ``output.parent.mkdir(parents=True, exist_ok=True)`` appeared in the PNG and PDF
    exporters; the SVG exporter delegated its write to the renderer; the PDF exporter wrote
    with ``output.write_bytes``. None was atomic, none had an overwrite policy, and none had
    a dry run -- so a failed 200-page export left a truncated artifact behind. Here there is
    one write path: a temporary file in the destination directory, ``flush``, ``os.fsync``,
    then an atomic rename onto the target. A failure leaves the target untouched and no
    temporary behind.

    The byte guarantee
    -----------------
    ``payload`` is written verbatim in binary. No encode, no newline translation, no appended
    trailing byte, no BOM. That is the one byte-level promise this package makes, and it is
    what the differential oracle rests on now that SVG generation lives in the render slice:
    ``sha256`` of the file equals ``sha256`` of the payload, asserted directly. It matters
    concretely -- the legacy renderer's own writer emitted
    ``<?xml version='1.0' encoding='utf-8'?>\n`` and *no* trailing newline, so a sink that
    appended one would shift all 30 manifest hashes by a byte and the failure would look
    like a renderer regression.

    What a dry run does and does not predict
    ---------------------------------------
    A dry run answers "where would these bytes go, and how many are there" and nothing else.
    It returns its receipt before the overwrite policy is consulted, so a dry run over a
    directory that already holds the artifacts reports ``committed=False`` for every page
    while the real run would raise ``ALREADY_PRESENT`` on the first. That is a deliberate
    choice with a cost, recorded rather than overlooked: raising during a dry run would abort
    the preview at page one, which is the opposite of what a preview is for, and
    :class:`ArtifactRef` has no field for "would have been refused" -- the honest fix is a
    domain change, not an adapter one. ``test_a_dry_run_does_not_refuse_an_existing_artifact``
    pins the current answer so a future change to it is a decision rather than a drift.

    ``ArtifactName`` is a stem, so the suffix is appended
    ---------------------------------------------------
    ``write(ArtifactName(value="out.pdf"), ..., media=PDF)`` lands ``out.pdf.pdf``, and that
    is deliberate rather than overlooked: :class:`ArtifactMedia` is documented as the sole
    source of the suffix, so silently swallowing a caller-supplied one would put a second
    opinion about the filename in the one component that owns it. A CLI turning
    ``--output /tmp/out.pdf`` into a destination plus a name must pass ``Path.stem``.
    """

    def __init__(self, *, destination: Path, overwrite: bool, dry_run: bool) -> None:
        self._destination = destination
        self._overwrite = overwrite
        self._dry_run = dry_run

    def write(self, name: ArtifactName, payload: bytes, *, media: ArtifactMedia) -> ArtifactRef:
        """Commit ``payload`` under ``name``.

        Parameters
        ----------
        name
            Caller-chosen artifact stem. Already validated by :class:`ArtifactName`, so this
            sink does not re-check separators or traversal.
        payload
            The complete artifact, written verbatim.
        media
            What kind of artifact this is, and the sole source of the suffix.

        Returns
        -------
        ArtifactRef
            Receipt echoing ``name`` verbatim, with a ``file:`` URI and ``committed`` false
            when the sink only simulated the write.

        Raises
        ------
        ArtifactWriteFailed
            The artifact could not be committed, with the sink's reason as a typed
            :class:`ArtifactWriteReason`.
        """
        target = self._destination / f"{name.value}{SUFFIXES[media]}"
        receipt = ArtifactRef(
            name=name,
            uri=target.absolute().as_uri(),
            byte_count=len(payload),
            media=media,
            committed=not self._dry_run,
        )
        if self._dry_run:
            return receipt
        if self._exists(target, name=name) and not self._overwrite:
            raise ArtifactWriteFailed(
                name=name.value,
                reason=ArtifactWriteReason.ALREADY_PRESENT,
                detail=f"{target} already exists and overwriting was not requested",
            )
        self._commit(target, payload, name=name)
        return receipt

    def _exists(self, target: Path, *, name: ArtifactName) -> bool:
        """Ask whether ``target`` is already taken, without letting an ``OSError`` escape.

        :meth:`pathlib.Path.exists` swallows only the errnos that mean "not found". On Python
        3.13 ``ENAMETOOLONG`` is not one of them, so a target name the filesystem will not
        accept made this probe raise a *raw* ``OSError`` out of :meth:`write` -- an untyped
        failure crossing a port whose whole contract is
        :class:`~rmspec.domain.errors.ArtifactWriteFailed`. Translating here also means such a
        name is refused before a directory is created or a temporary is opened.

        Parameters
        ----------
        target
            The final location.
        name
            The artifact's name, for the error message.

        Returns
        -------
        bool
            Whether something already holds the target name.

        Raises
        ------
        ArtifactWriteFailed
            The question itself could not be answered -- an illegal name, or a component of
            the destination that cannot be traversed.
        """
        try:
            return target.exists()
        except OSError as exc:
            raise self._failure(name, exc) from exc

    def _commit(self, target: Path, payload: bytes, *, name: ArtifactName) -> None:
        """Write ``payload`` to ``target`` atomically.

        Parameters
        ----------
        target
            Final location.
        payload
            Bytes to write.
        name
            The artifact's name, for the error message.

        Raises
        ------
        ArtifactWriteFailed
            The destination could not be created, the bytes could not be written, or the
            rename failed -- including because ``target``'s own name exceeds the
            filesystem's ``NAME_MAX``, which is the one length limit this sink has and the
            same one the legacy direct write had. The temporary is removed first, so nothing
            partial survives, and the target keeps whatever it held before.
        """
        try:
            self._destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._failure(name, exc) from exc
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._destination,
                prefix=TEMPORARY_PREFIX,
                suffix=TEMPORARY_SUFFIX,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise self._failure(name, exc) from exc

    def _failure(self, name: ArtifactName, exc: OSError) -> ArtifactWriteFailed:
        """Translate an ``OSError`` into the domain's typed write failure.

        Parameters
        ----------
        name
            The artifact's name.
        exc
            The operating-system error.

        Returns
        -------
        ArtifactWriteFailed
            The domain error to raise, carrying a closed reason rather than an errno.
        """
        reason = _ERRNO_REASONS.get(exc.errno or 0, ArtifactWriteReason.INTERRUPTED)
        return ArtifactWriteFailed(name=name.value, reason=reason, detail=str(exc))
