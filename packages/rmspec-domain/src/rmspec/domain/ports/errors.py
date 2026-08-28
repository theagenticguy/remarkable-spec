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
    """Answer whether an optional third-party module is present, and whether it loads.

    The legacy CLI hid 27 function-local imports so that missing extras stayed out
    of startup. The cost was that ``ImportError`` surfaced mid-command, after the
    user had already paid for a render or a device round trip, and named a module
    rather than the extra that ships it. This port moves the question to the front:
    the composition root folds the probe over the modules an adapter needs and
    raises before any use case runs, naming the module and the extra that provides
    it as structured fields.

    A probe is required because a dependency-injection container resolves providers
    lazily; nothing fires "at composition" on its own. It is a port, not a helper,
    because the alternative is monkeypatching the import system: a test that must
    exercise the missing-extra path cannot uninstall a package from the environment
    it is running in, but it can bind a probe over a set of names.

    Two methods rather than one predicate, because an optional module has three
    states and a ``bool`` only has room for two:

    absent
        No module of that name is installed. :meth:`is_installed` is ``False``.
    importable
        Installed and it loads. :meth:`is_installed` is ``True`` and
        :meth:`load_error` is ``None``.
    present but unloadable
        The wheel is installed and a native library it links against is not, so a
        spec is discoverable and the import still fails. :meth:`is_installed` is
        ``True`` and :meth:`load_error` carries the loader's message.

    The third state is the live one on this project, not a hypothetical:
    ``cairocffi`` from the ``render`` extra resolves and then dies in ``dlopen``
    with ``OSError: no library called "cairo-2" was found``, which is why the legacy
    tree mutated ``DYLD_FALLBACK_LIBRARY_PATH`` at import time and why its debugging
    guide documents "``[render]`` extra is installed, yet cairo still fails to
    load". ``weasyprint`` (pango, cairo), ``Quartz`` (pyobjc frameworks) and
    ``fitz`` fail the same way. A single spec-only predicate would report those as
    available, the container's eager pass would raise nothing, and the ``OSError``
    would land mid-command three call sites deep naming a C library instead of an
    extra -- the precise hole the 27 lazy imports left open.

    Notes
    -----
    Scope is ``APP``. Neither answer can change inside one process, so the container
    resolves a single probe and shares it, and an adapter may memoize per module
    name.

    Composition-root protocol. For every module a selected provider needs, call
    :meth:`is_installed`; for the subset that loads native code, and only for
    providers actually being composed, call :meth:`load_error` as well. Collect the
    whole set of failures before raising, so a user missing two extras learns both
    from one run rather than one per attempt. ``rmspec --help`` composes no render
    provider and therefore probes nothing; a render command was going to import
    ``cairocffi`` anyway, so the eager :meth:`load_error` moves that cost earlier
    instead of adding it -- and leaves the module in ``sys.modules``, so the
    adapter's own import is free.

    Raised errors are not this port's business: the probe reports, the caller
    raises. That keeps the import-name-to-extra mapping in one reviewed place
    instead of once per adapter, and it means adapters need no first-use
    ``ImportError``/``OSError`` handling for probed modules, because
    :meth:`load_error` performs the real import and so already observes a
    transitive ``dlopen`` failure.

    That mapping is composition-root data, not domain data: a ``doctor`` or
    capabilities use case takes ``Mapping[str, tuple[str, str]]`` of import name to
    extra and feature as an argument rather than the domain owning a table of
    third-party names it is forbidden to import.

    One gap this port hands to the errors slice.
    :class:`~rmspec.domain.errors.MissingDependencyError` fits the absent state
    only: its message reads "is not installed" and its remediation is ``uv sync
    --extra <extra>``, both wrong for a wheel that is installed and whose dylib is
    missing, and its ``package``/``extra``/``feature`` fields have no slot for the
    string :meth:`load_error` returns. The unloadable state therefore needs a
    sibling error carrying package, extra and loader detail; until it exists the
    detail has nowhere structured to go.
    """

    def is_installed(self, module_name: str, /) -> bool:
        """Report whether a module of this name is installed in this interpreter.

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
            ``True`` when a module spec of that name is discoverable on
            ``sys.path``, ``False`` when none is. ``True`` is a statement about
            installation, not about loading: ``find_spec("cairocffi")`` returns a
            spec on a host with no ``libcairo``, where the import then raises
            ``OSError``. Whether the import succeeds is :meth:`load_error`'s
            question.

        Notes
        -----
        The call is total. An unknown, misspelled or otherwise unresolvable name is
        reported as ``False`` and never raised: implementations must swallow
        ``ImportError`` and ``ModuleNotFoundError`` from a missing parent package,
        and the ``ValueError`` raised for a name already in ``sys.modules`` with
        ``__spec__`` set to ``None``. A failure of the probe can never be mistaken
        for a failure of the command being probed.

        Implementations must not execute module code to answer -- ``find_spec`` is
        the production adapter -- because this is the question a bare install and a
        capabilities listing ask about features the user did not request, and
        importing a native extension to answer it is the cost this port avoids.
        """
        ...

    def load_error(self, module_name: str, /) -> str | None:
        """Report why importing ``module_name`` fails, or ``None`` when it succeeds.

        The one question that cannot be answered without executing module code, so
        this method does import it. Callers restrict it to modules a provider being
        composed is about to import anyway, which is what keeps the prohibition in
        :meth:`is_installed` meaningful.

        Parameters
        ----------
        module_name
            Top-level import name, under the same rules as :meth:`is_installed`.

        Returns
        -------
        str | None
            ``None`` when the import completed. Otherwise a non-empty single-line
            reason taken from the raised exception, such as ``no library called
            "cairo-2" was found`` -- the loader's own words, which name the C
            library the extra's wheel does not carry. An exception with an empty
            message is reported as its class name, so the return value is never an
            empty string and a caller never has to render a blank explanation.

        Notes
        -----
        The call is total. Implementations must catch ``ImportError``,
        ``ModuleNotFoundError``, ``OSError`` and any exception a module's top-level
        code raises, and return its message: a probe asked to describe a failure
        must not abort composition with that same failure.

        For an absent module this returns the ``ModuleNotFoundError`` message, so a
        caller needing only usable-or-not can call this alone. Pairing it with
        :meth:`is_installed` is what separates absent from present-but-unloadable.
        The two answers must agree -- for every name, ``is_installed(name) is
        False`` implies ``load_error(name) is not None`` -- which is the invariant a
        test double is checked against.
        """
        ...
