"""Ports owned by the ``errors`` slice of the domain.

The slice contributes exactly one port, :class:`DependencyProbe`. Everything else
it owns -- the ``RmspecError`` tree, the ``Degradation`` model, ``DegradationKind``
and the ``exit_code`` table -- is data or pure policy that inverts no dependency on
a technology, so it lives in :mod:`rmspec.domain.errors` rather than here.

Notes
-----
Four ports proposed for this slice were dropped rather than shipped, and the reasons
are recorded here so they are not re-proposed:

``DocumentCatalog`` / ``DocumentContent``
    They belong to the catalog slice, not to errors, and every variant leaked an
    adapter shape across the boundary: ``pathlib.Path`` members, raw ``.rm`` bytes
    that only ``rmscene`` can interpret, a content-type/body pair lifted from an
    HTTP response, and errors named after xochitl files. Two of the three proposals
    also listed a device adapter that could implement only one of three methods --
    the ``Protocol`` was the filesystem adapter wearing a port's name.
``DegradationSink``
    ``drain()`` promoted a test double's in-memory buffer into the contract, so the
    write-through adapters had to return an empty list and silently break the caller
    that prints the summary; the variants then disagreed over whether recording may
    raise, which made app control flow depend on which double was injected.
    Degradations instead travel back inside use-case results as a list of frozen
    ``Degradation`` models, which the sync engine already proves in-tree, and
    ``--strict`` becomes one check at the CLI boundary.
``DocumentSelector``
    Candidate ranking is four lines of pure policy over records, so a ``Protocol``
    over it inverts nothing; the one variant with real I/O was a terminal prompt,
    which the CLI owns. The app raises an ambiguity error carrying the candidates
    and the CLI decides what to do about a human.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["DependencyProbe"]


class DependencyProbe(Protocol):
    """Answer whether an optional third-party module is importable in this process.

    The legacy CLI hid 27 function-local imports so that missing extras stayed out
    of startup. The cost was that ``ImportError`` surfaced mid-command, after the
    user had already paid for a render or a device round trip, and named a module
    rather than the extra that ships it. This port moves the question to the front:
    the composition root folds the probe over the modules an adapter needs and
    raises ``MissingDependencyError`` -- carrying the module name and the extra that
    provides it as structured fields -- before any use case runs.

    A probe is required because a dependency-injection container resolves providers
    lazily; nothing fires "at composition" on its own. It is a port, not a helper,
    because the alternative is monkeypatching the import system: a test that must
    exercise the missing-extra path cannot uninstall a package from the environment
    it is running in, but it can bind a probe over a set of names.

    Notes
    -----
    Scope is ``APP``. The answer cannot change inside one process, so the container
    resolves a single probe and shares it.

    The production adapter is built on ``importlib.util.find_spec``, which locates a
    module without executing it. Probing therefore costs no import time and no
    native library loading, which is what keeps ``rmspec --help`` and the commands
    that need no extras working on a bare install -- the property the 27 lazy
    imports existed to protect.

    Raised errors are not this port's business: the probe reports, the caller
    raises. That keeps the module-to-extra mapping in one reviewed place instead of
    once per adapter.
    """

    def is_installed(self, module_name: str, /) -> bool:
        """Report whether ``module_name`` can be imported in this interpreter.

        Parameters
        ----------
        module_name
            Top-level import name to probe, such as ``"boto3"``, ``"httpx"`` or
            ``"cairocffi"``. Not a dotted submodule path and not a distribution
            name on PyPI -- the two differ often enough (``pyobjc`` ships
            ``Quartz``, ``pymupdf`` ships ``fitz``) that the caller is expected to
            pass the name it would actually import.

        Returns
        -------
        bool
            ``True`` when an import of ``module_name`` would succeed, ``False``
            when the module is absent from this environment.

        Notes
        -----
        The call is total. An unknown, misspelled or otherwise unimportable name is
        reported as ``False`` and never raised, so a failure of the probe can never
        be mistaken for a failure of the command being probed. Implementations must
        not execute module code to answer, because importing a native extension for
        a feature the user did not ask for is the cost this port exists to avoid.
        """
        ...
