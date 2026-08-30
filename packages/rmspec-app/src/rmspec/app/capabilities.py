"""Make the transport asymmetries discoverable, so none of them is a runtime surprise.

New in v0.2.0, and worth its own module. The device slice expresses capability asymmetry as
*which ports exist*: a use case that needs to write declares
:class:`~rmspec.domain.ports.device.DocumentUploader` and the composition root either binds
one or raises. That is the right shape for correctness and a terrible shape for discovery --
a user learns what their transport cannot do by being refused mid-command. This use case
reports the same facts up front: which ports the bound transport serves, and for the rest,
what it will refuse and which transport to retry with.

It is a rendering, not a new vocabulary
---------------------------------------
:class:`~rmspec.domain.errors.DeviceOperationUnsupported` already carries ``operation`` and
``supported_by: tuple[TransportKind, ...]``, which is exactly the shape of "this is not
possible here; it needs that". So each refusal reported below is built by constructing that
error and reporting its own assembled message verbatim. Constructing it rather than
paraphrasing it is the point: the sentence a user reads in a capability report is the
sentence they would have hit at the call site, and the two cannot drift into disagreeing
about the same transport.

Why there is no matrix in this file
----------------------------------
Every fact below arrives in :class:`ReportCapabilitiesRequest`. The composition root is the
only thing that knows what it bound, and a table here would be a second source of truth that
keeps asserting "no USB uploader" after a firmware or an adapter learns otherwise -- the
mistake this project has already made twice in prose, once about ``.rmdoc`` over USB and once
about the trash as a parent value, both refuted by re-measurement. A report that can only
repeat what it was given cannot be wrong about a binding that changed.

The facts the shape has to be able to express, all measured on firmware 3.27.3.0, and none of
them encoded here:

* **``POST /upload`` cannot target a folder.** No parameter for a destination exists and the
  document lands at the root, so a USB uploader handed a ``parent_uuid`` raises instead of
  silently root-placing. One limit on a *bound* port.
* **``POST /upload`` is create-only.** It re-keys both the document and the page uuids on
  import, so it can add a document but never update one; editing an existing page needs SSH.
  A second limit on the same bound port, which is why limits are a tuple.
* **``SearchIndexSource`` has no USB binding at all.** The firmware's HTTP route table is
  closed at six families and none of them serves a file from the xochitl tree. An *unbound*
  port, reported with the reason rather than as a silent absence.
* **A USB ``DeviceFactsSource`` reports most fields unsupported**, where SSH answers firmware
  and model. A bound port that answers partially -- and the serial is the case where
  ``supported_by`` is legitimately empty, because no transport can read it.
* **There is no local-mirror uploader**, because a mirror is not a device. The same unbound
  shape as the search index, under a different transport, with both real transports named as
  the alternatives.

What "unbound, and here is why" is made of
-----------------------------------------
:attr:`PortBinding.bound` says whether the container bound the port;
:attr:`PortBinding.limits` says what it cannot do. A model validator rejects an unbound
binding with no limits, so "this port is missing and nobody said why" is unconstructible
rather than merely discouraged -- the reason
:class:`~rmspec.domain.ports.device.DeviceListing` gives ``skipped`` no default.

Reading no port is the other half of the design
-----------------------------------------------
This is the one use case with no collaborator. It could have asked a bound
:class:`~rmspec.domain.ports.device.DeviceFactsSource` which fields it reports as
unsupported, which would be a real observation rather than a description -- and it would
mean ``rmspec device capabilities`` needs the tablet attached. A report whose whole purpose
is telling a user what to expect *before* they commit to a transport must not require that
transport to be live, so the facts arrive as data and nothing here opens a session.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, model_validator

from rmspec.domain.errors import Degradation, DeviceOperationUnsupported, TransportKind

__all__ = [
    "OperationLimit",
    "PortBinding",
    "RefusedOperation",
    "ReportCapabilities",
    "ReportCapabilitiesRequest",
    "ReportCapabilitiesResult",
]


class OperationLimit(BaseModel, frozen=True, extra="forbid"):
    """One thing a transport cannot do, named the way the error tree names it."""

    operation: str
    """What cannot be done, as an imperative phrase.

    Goes verbatim into :class:`~rmspec.domain.errors.DeviceOperationUnsupported`, whose
    message reads "<operation> is not possible over <transport>; it needs <alternatives>", so
    "place a document in a folder" belongs here and "parent_uuid" does not.
    """

    detail: str
    """The measured reason, for a human deciding whether to change transports.

    Displayed and logged, never parsed: this is where "the import route has no destination
    parameter" or "the route table is closed at six families" is said.
    """

    supported_by: tuple[TransportKind, ...]
    """Transports that can do this, which may be empty.

    Empty is a real answer rather than a missing one -- no transport can read the device
    serial -- and it is what makes the assembled refusal say "it needs no transport" instead
    of naming an alternative that does not exist.
    """


class PortBinding(BaseModel, frozen=True, extra="forbid"):
    """What the composition root did about one port, and what that costs the caller."""

    port: str
    """The Protocol's name, such as ``DocumentUploader``. The unit a container binds."""

    bound: bool
    """Whether the container bound an implementation of this port for this transport."""

    limits: tuple[OperationLimit, ...]
    """What the binding cannot do. Empty only when the port is bound and total.

    Two limits on one bound port is the ordinary case, not an edge one: the USB uploader
    refuses a destination folder *and* refuses to update an existing document, and a caller
    hitting either deserves the specific reason for that one.
    """

    @model_validator(mode="after")
    def _check_absence_is_explained(self) -> Self:
        """Reject an unbound port that says nothing about why it is unbound.

        Returns
        -------
        Self
            The validated binding.

        Raises
        ------
        ValueError
            The port is not bound and no limit explains it, which is the silent absence this
            whole use case exists to replace.
        """
        if not self.bound and not self.limits:
            msg = f"port {self.port} is unbound and no limit says why"
            raise ValueError(msg)
        return self


class RefusedOperation(BaseModel, frozen=True, extra="forbid"):
    """One thing the bound transport will refuse, in the error tree's own words."""

    port: str
    """The Protocol whose binding refuses this."""

    operation: str
    """What will be refused, as :attr:`OperationLimit.operation` phrased it."""

    detail: str
    """The measured reason, from :attr:`OperationLimit.detail`."""

    supported_by: tuple[TransportKind, ...]
    """Transports that can do it. Empty when none can."""

    refusal: str
    """The exact message :class:`~rmspec.domain.errors.DeviceOperationUnsupported` assembles.

    Verbatim, so the sentence a capability report prints is the sentence the failed call
    would have printed. Nothing here re-words it.
    """


class ReportCapabilitiesRequest(BaseModel, frozen=True, extra="forbid"):
    """The transport in use and what the composition root bound for it."""

    transport: TransportKind
    """Which way of reaching the tablet this report is about."""

    bindings: tuple[PortBinding, ...]
    """One entry per port the container considered, bound or not.

    Order is the caller's and is preserved: a composition root that lists the read side
    before the write side gets a report in that order.
    """


class ReportCapabilitiesResult(BaseModel, frozen=True, extra="forbid"):
    """Every binding, partitioned into what works, what is limited, and what is absent.

    No field has a default. A capability report constructed without stating what is absent
    would claim a transport is total, which is the exact surprise this use case removes.
    """

    transport: TransportKind
    """The transport this report is about, echoed so the result stands alone in JSON."""

    served: tuple[str, ...]
    """Names of the ports this transport serves with no limit, in request order."""

    restricted: tuple[RefusedOperation, ...]
    """One row per limit on a *bound* port, in request order.

    The port works; this particular request will not. A caller that never asks for a
    destination folder never meets the USB uploader's first row.
    """

    unavailable: tuple[RefusedOperation, ...]
    """One row per limit on an *unbound* port, in request order.

    Nothing was bound, so every call fails, and the rows say why and where to go instead.
    """

    degradations: tuple[Degradation, ...]
    """Always empty here, and required anyway.

    Nothing is substituted: this use case reads no device and makes no choice on a caller's
    behalf. :class:`~rmspec.domain.errors.DegradationKind` is closed and has no member for
    "a capability was reported", and inventing one is a reviewed change to the domain.
    """


def _refused(
    limit: OperationLimit,
    /,
    *,
    port: str,
    transport: TransportKind,
) -> RefusedOperation:
    """Turn one limit into a report row carrying the error tree's own sentence.

    Constructs :class:`~rmspec.domain.errors.DeviceOperationUnsupported` without raising it,
    purely to read the message it assembles. That is what keeps the discoverable wording and
    the runtime wording identical: there is one place that phrases this refusal, and it is
    the domain.

    Parameters
    ----------
    limit
        What the transport cannot do, and which transports can.
    port
        The Protocol whose binding is limited.
    transport
        The transport this report is about.

    Returns
    -------
    RefusedOperation
        The limit, plus the assembled refusal message.
    """
    refusal = DeviceOperationUnsupported(
        transport=transport,
        operation=limit.operation,
        supported_by=limit.supported_by,
    )
    return RefusedOperation(
        port=port,
        operation=limit.operation,
        detail=limit.detail,
        supported_by=limit.supported_by,
        refusal=refusal.message,
    )


class ReportCapabilities:
    """Report what the bound transport can do, and what it will refuse, without asking it.

    Pure policy over a description. It takes no collaborator, opens no session, writes to no
    stream and holds no state, so the report is available with the tablet unplugged -- which
    is when a user most wants it.

    Notes
    -----
    A USB composition root describes itself, and gets back the three-way split::

        result = reporter.report(
            ReportCapabilitiesRequest(
                transport=TransportKind.USB_WEB_API,
                bindings=(
                    PortBinding(port="DeviceCatalog", bound=True, limits=()),
                    PortBinding(
                        port="SearchIndexSource",
                        bound=False,
                        limits=(
                            OperationLimit(
                                operation="read the on-device search index",
                                detail="the route table is closed at six families",
                                supported_by=(TransportKind.SSH,),
                            ),
                        ),
                    ),
                ),
            )
        )
    """

    def __init__(self) -> None:
        """Take no collaborator, and declare that by having a constructor at all.

        Spelled out rather than inherited from ``object`` so that the keyword-only
        collaborator rule is satisfied by a signature that names none, instead of by
        ``*args, **kwargs``. See this module's docstring for why reading a port here would
        defeat the purpose.
        """

    def report(self, request: ReportCapabilitiesRequest, /) -> ReportCapabilitiesResult:
        """Partition the described bindings into served, restricted and unavailable.

        Parameters
        ----------
        request
            The transport in use and one binding per port the container considered.

        Returns
        -------
        ReportCapabilitiesResult
            The ports that work, one row per limit on a bound port, and one row per limit on
            an unbound one -- each row carrying the refusal the domain would assemble.
        """
        served: list[str] = []
        restricted: list[RefusedOperation] = []
        unavailable: list[RefusedOperation] = []
        for binding in request.bindings:
            if binding.bound and not binding.limits:
                served.append(binding.port)
                continue
            rows = [
                _refused(limit, port=binding.port, transport=request.transport)
                for limit in binding.limits
            ]
            if binding.bound:
                restricted.extend(rows)
            else:
                unavailable.extend(rows)
        return ReportCapabilitiesResult(
            transport=request.transport,
            served=tuple(served),
            restricted=tuple(restricted),
            unavailable=tuple(unavailable),
            degradations=(),
        )
