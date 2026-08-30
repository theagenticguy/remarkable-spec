"""A deliberately partial set of bindings, the five measured asymmetries, and no matrix.

Nothing is bound here, because there is nothing to bind: this is the one use case with no
collaborator, by the design its module docstring defends -- a capability report a user
consults before committing to a transport must not need that transport live. So the tests
below supply the bindings as data, exactly as a composition root would, and no port Protocol
is faked.

That also makes the central property testable: **the module owns no matrix**. Every fact in
this file is stated by the test and repeated by the report, and
``test_a_binding_this_project_believes_impossible_is_still_reported_as_given`` proves it by
describing a USB search index -- something the firmware cannot serve today -- and watching the
report agree, rather than correcting it from a table of its own.

Two of the five measured facts are per-request limits on a *bound* port and three are absent
bindings, which is why the report is a three-way split rather than a boolean.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from rmspec.app.capabilities import (
    OperationLimit,
    PortBinding,
    RefusedOperation,
    ReportCapabilities,
    ReportCapabilitiesRequest,
    ReportCapabilitiesResult,
)
from rmspec.domain.errors import DeviceOperationUnsupported, TransportKind, exit_code

USB = TransportKind.USB_WEB_API
SSH = TransportKind.SSH
MIRROR = TransportKind.LOCAL_MIRROR

# ── The five measured asymmetries, stated here because the use case may not know them. ──

NO_DESTINATION = OperationLimit(
    operation="place a document in a folder",
    detail="POST /upload has no destination parameter; the document lands at the root",
    supported_by=(SSH,),
)
CREATE_ONLY = OperationLimit(
    operation="update an existing page",
    detail="POST /upload re-keys the document and page uuids on import, so it can only create",
    supported_by=(SSH,),
)
NO_USB_SEARCH_INDEX = OperationLimit(
    operation="read the on-device search index",
    detail="the firmware's route table is closed at six families and none serves a file",
    supported_by=(SSH,),
)
NO_MIRROR_UPLOAD = OperationLimit(
    operation="place a document on the device",
    detail="a mirror is an already-pulled copy of a store, not a device",
    supported_by=(USB, SSH),
)
NO_SERIAL_ANYWHERE = OperationLimit(
    operation="read the device serial",
    detail="the serial exists only in the secret-bearing config no code path here may open",
    supported_by=(),
)

USB_CATALOG = PortBinding(port="DeviceCatalog", bound=True, limits=())
USB_UPLOADER = PortBinding(
    port="DocumentUploader",
    bound=True,
    limits=(NO_DESTINATION, CREATE_ONLY),
)
USB_SEARCH_INDEX = PortBinding(
    port="SearchIndexSource",
    bound=False,
    limits=(NO_USB_SEARCH_INDEX,),
)
USB_FACTS = PortBinding(port="DeviceFactsSource", bound=True, limits=(NO_SERIAL_ANYWHERE,))
MIRROR_UPLOADER = PortBinding(port="DocumentUploader", bound=False, limits=(NO_MIRROR_UPLOAD,))


def _report(
    *bindings: PortBinding,
    transport: TransportKind = USB,
) -> ReportCapabilitiesResult:
    return ReportCapabilities().report(
        ReportCapabilitiesRequest(transport=transport, bindings=bindings)
    )


def _rows(reported: tuple[RefusedOperation, ...]) -> list[tuple[str, str]]:
    return [(row.port, row.operation) for row in reported]


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error.

    Same reason ``test_app_resolve.py`` gives: the type gate rejects a direct attribute
    assignment before pydantic can be shown rejecting it, and no ``type: ignore`` is allowed.
    """
    setattr(target, field, value)


# ───────────────────────────── the three-way split ─────────────────────────────


def test_a_port_that_is_bound_and_total_is_simply_served():
    result = _report(USB_CATALOG)
    assert result.served == ("DeviceCatalog",)
    assert result.restricted == ()
    assert result.unavailable == ()


def test_a_bound_port_with_a_limit_is_restricted_rather_than_served():
    """The port works; this particular request will not, and the caller may never make it."""
    result = _report(USB_UPLOADER)
    assert result.served == ()
    assert _rows(result.restricted) == [
        ("DocumentUploader", "place a document in a folder"),
        ("DocumentUploader", "update an existing page"),
    ]


def test_an_unbound_port_is_unavailable_and_says_why():
    result = _report(USB_SEARCH_INDEX)
    (row,) = result.unavailable
    assert row.port == "SearchIndexSource"
    assert "route table is closed at six families" in row.detail
    assert row.supported_by == (SSH,)


def test_a_partial_set_of_bindings_reports_all_three_states_at_once():
    result = _report(USB_CATALOG, USB_UPLOADER, USB_SEARCH_INDEX, USB_FACTS)
    assert result.served == ("DeviceCatalog",)
    assert _rows(result.restricted) == [
        ("DocumentUploader", "place a document in a folder"),
        ("DocumentUploader", "update an existing page"),
        ("DeviceFactsSource", "read the device serial"),
    ]
    assert _rows(result.unavailable) == [("SearchIndexSource", "read the on-device search index")]


def test_the_report_keeps_the_order_the_composition_root_described():
    result = _report(USB_FACTS, USB_UPLOADER)
    assert [row.port for row in result.restricted] == [
        "DeviceFactsSource",
        "DocumentUploader",
        "DocumentUploader",
    ]


def test_a_transport_with_nothing_described_reports_nothing():
    """The proof that no matrix lives in the module: with no input there is no output."""
    result = _report(transport=SSH)
    assert result.served == ()
    assert result.restricted == ()
    assert result.unavailable == ()
    assert result.transport is SSH


# ─────────────────── the five measured asymmetries, as this shape ───────────────────


def test_the_usb_uploader_refuses_a_destination_rather_than_root_placing():
    """Silently placing at the root is what the port forbids; this is how a user learns."""
    (row, _) = _report(USB_UPLOADER).restricted
    assert row.operation == "place a document in a folder"
    assert "no destination parameter" in row.detail
    assert row.supported_by == (SSH,)


def test_the_usb_uploader_is_create_only_because_import_re_keys_every_uuid():
    (_, row) = _report(USB_UPLOADER).restricted
    assert row.operation == "update an existing page"
    assert "re-keys" in row.detail
    assert row.supported_by == (SSH,)


def test_the_search_index_has_no_usb_binding_at_all():
    result = _report(USB_SEARCH_INDEX)
    assert result.served == ()
    assert result.restricted == ()
    assert _rows(result.unavailable) == [("SearchIndexSource", "read the on-device search index")]


def test_a_fact_no_transport_can_answer_names_no_alternative():
    """The serial is readable from nowhere, so "retry over SSH" would be a lie."""
    (row,) = _report(USB_FACTS).restricted
    assert row.supported_by == ()
    assert "it needs no transport" in row.refusal


def test_a_local_mirror_has_no_uploader_because_a_mirror_is_not_a_device():
    result = _report(MIRROR_UPLOADER, transport=MIRROR)
    (row,) = result.unavailable
    assert row.supported_by == (USB, SSH)
    assert "over local_mirror" in row.refusal
    assert "usb_web_api, ssh" in row.refusal


def test_the_same_port_reports_differently_under_two_transports():
    """Which is the whole reason the report is per-transport rather than per-port."""
    over_usb = _report(USB_UPLOADER)
    over_mirror = _report(MIRROR_UPLOADER, transport=MIRROR)
    assert over_usb.served == over_mirror.served == ()
    assert _rows(over_usb.restricted) == [
        ("DocumentUploader", "place a document in a folder"),
        ("DocumentUploader", "update an existing page"),
    ]
    assert over_usb.unavailable == ()
    assert _rows(over_mirror.unavailable) == [
        ("DocumentUploader", "place a document on the device")
    ]
    assert over_mirror.restricted == ()


# ──────────────── the refusal is the error tree's sentence, not a paraphrase ────────────────


def test_a_reported_refusal_is_the_message_the_call_site_would_have_raised():
    """One phrasing, in the domain, so a report and a failure cannot disagree."""
    (row,) = _report(USB_SEARCH_INDEX).unavailable
    raised = DeviceOperationUnsupported(
        transport=USB,
        operation=NO_USB_SEARCH_INDEX.operation,
        supported_by=NO_USB_SEARCH_INDEX.supported_by,
    )
    assert row.refusal == raised.message


def test_a_reported_refusal_names_the_transport_in_use_and_the_alternatives():
    (row,) = _report(USB_SEARCH_INDEX).unavailable
    assert row.refusal == (
        "read the on-device search index is not possible over usb_web_api; it needs ssh"
    )


def test_the_refusal_this_report_previews_is_a_configuration_exit_status():
    """``EX_CONFIG``: the wiring is wrong, not the request -- which is why previewing helps."""
    raised = DeviceOperationUnsupported(
        transport=USB,
        operation=NO_USB_SEARCH_INDEX.operation,
        supported_by=NO_USB_SEARCH_INDEX.supported_by,
    )
    assert exit_code(raised) == 78


# ──────────────── "unbound and nobody said why" is unconstructible ────────────────


def test_an_unbound_port_with_no_limit_cannot_be_described():
    with pytest.raises(ValidationError, match="unbound and no limit says why"):
        PortBinding(port="SearchIndexSource", bound=False, limits=())


def test_a_bound_port_with_no_limit_is_perfectly_constructible():
    """The validator constrains absence only; a total port has nothing to explain."""
    assert PortBinding(port="DeviceCatalog", bound=True, limits=()).limits == ()


# ─────────────────────── no table of its own, in either direction ───────────────────────


def test_a_binding_this_project_believes_impossible_is_still_reported_as_given():
    """A hardcoded matrix would contradict the container the moment a binding changed."""
    result = _report(PortBinding(port="SearchIndexSource", bound=True, limits=()))
    assert result.served == ("SearchIndexSource",)
    assert result.unavailable == ()


def test_a_port_the_use_case_has_never_heard_of_is_reported_all_the_same():
    result = _report(PortBinding(port="FutureThumbnailSource", bound=True, limits=()))
    assert result.served == ("FutureThumbnailSource",)


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_this_use_case_takes_no_collaborator_and_says_so_in_its_signature():
    """A capability report that needed the tablet attached would defeat its own purpose."""
    parameters = list(inspect.signature(ReportCapabilities.__init__).parameters.values())
    assert [parameter.name for parameter in parameters] == ["self"]


def test_nothing_here_is_ever_reported_as_a_substitution():
    assert _report(USB_CATALOG, USB_SEARCH_INDEX).degradations == ()


def test_a_result_is_frozen():
    result = _report(USB_CATALOG)
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "served", ())


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ReportCapabilitiesResult.model_validate(
            {
                "transport": USB,
                "served": (),
                "restricted": (),
                "unavailable": (),
                "degradations": (),
                "matrix": (),
            }
        )


def test_a_limit_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        OperationLimit.model_validate(
            {
                "operation": "place a document in a folder",
                "detail": "no destination parameter",
                "supported_by": (SSH,),
                "remediation": "retry with ssh",
            }
        )


def test_a_request_names_a_transport_from_the_closed_set():
    with pytest.raises(ValidationError):
        ReportCapabilitiesRequest.model_validate({"transport": "bluetooth", "bindings": ()})
