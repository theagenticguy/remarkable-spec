"""The package's public surface, pinned as an invariant rather than a convention.

``rmspec.device`` exports two transports and seven adapters, and everything that decodes a
wire format lives behind a leading underscore. That is held in place by nothing but module
naming and a hand-written ``__all__``, and the obvious next change -- "let the CLI show which
entries were skipped and why" -- would re-export ``rmspec.device._wire.DecodedEntries`` and
put the listing decoder's own value type in the public surface with no gate failing.

Two properties matter more than tidiness here.

**No private module leaks.** ``_errors``, ``_wire``, ``_pages``, ``_archive`` and ``_shell``
are the modules that own firmware knowledge. ``_shell`` in particular declares
``PathUnreadableError``, which is deliberately *not* a domain error and whose own docstring
says nothing in this package's public surface may let it escape -- so it must not be exported
either.

**No double leaks.** ``rmspec.device.testing`` ships in the wheel, which is the point, but it
is imported explicitly and never re-exported, so a production import of this package cannot
pull an in-memory adapter into a name a composition root might bind by accident.

The subpackage gets the same treatment, because it ships too.
"""

from __future__ import annotations

from types import ModuleType

import pytest

import rmspec.device
import rmspec.device.testing

EXPECTED = [
    "ParamikoShell",
    "SshBundleSource",
    "SshCatalog",
    "SshFacts",
    "SshUploader",
    "UsbBundleSource",
    "UsbCatalog",
    "UsbFacts",
    "UsbWebApi",
]

EXPECTED_TESTING = [
    "IN_MEMORY_ENDPOINT",
    "IN_MEMORY_TRANSPORT",
    "UPLOAD_OPERATION",
    "FakeRemoteShell",
    "InMemoryDeviceCatalog",
    "InMemoryDeviceFactsSource",
    "InMemoryDocumentUploader",
    "InMemoryRawBundleSource",
]

#: ``from __future__ import annotations`` binds ``annotations`` as a module attribute. It is a
#: language artifact every module in the workspace carries, not something this package exports,
#: so it is named here rather than quietly filtered by a broader rule that would also hide a
#: real leak.
LANGUAGE_ARTIFACTS = {"annotations"}

#: Submodules a reader may import by name. ``addresses`` is public because a composition root
#: needs ``Endpoint`` and ``RemotePath``; ``usb`` and ``ssh`` because the adapters' own
#: constants are asserted against elsewhere; ``testing`` because the doubles ship. Every one
#: of them is bound on the package as a side effect of an import somewhere, which is why this
#: is a permitted set rather than a required one.
PUBLIC_MODULES = {"addresses", "ssh", "testing", "usb"}

#: Names the private modules define that must never become part of this surface. Each is a
#: wire-format or transport-internal value, and the middle three are the reason the modules
#: carry an underscore at all.
INTERNAL_NAMES = {
    "ArchiveMembers",
    "DecodedEntries",
    "PageOrderEntry",
    "PathUnreadableError",
    "RemoteShell",
    "command_failed",
    "decode_entries",
    "decode_page_order",
    "read_rmdoc",
    "translate_http",
    "translate_httpx",
    "translate_ssh",
}

#: What each exported name claims to be. A port binding claims its port's methods; a transport
#: claims its own verbs. Asserting the method set is what makes "is what it claims" a test
#: rather than a spelling check -- an ``__init__`` that re-exported the wrong sibling would
#: otherwise pass every other assertion in this file.
CLAIMED_METHODS = {
    "ParamikoShell": ("close", "connect", "list_dir", "read_file", "run", "write_file"),
    "SshBundleSource": ("load_bundle",),
    "SshCatalog": ("get_document", "list_documents"),
    "SshFacts": ("read_facts", "read_resources"),
    "SshUploader": ("upload",),
    "UsbBundleSource": ("load_bundle",),
    "UsbCatalog": ("get_document", "list_documents"),
    "UsbFacts": ("read_facts", "read_resources"),
    "UsbWebApi": ("get", "head"),
}


def _public_attributes(module: ModuleType) -> dict[str, object]:
    """Return every attribute a reader would call part of *module*'s surface."""
    return {
        name: getattr(module, name)
        for name in dir(module)
        if not name.startswith("_") and name not in LANGUAGE_ARTIFACTS
    }


def _public_values(module: ModuleType) -> dict[str, object]:
    """Return the public attributes that are not submodules.

    Submodules are filtered by *type* rather than by name because they are bound on the
    package as a side effect of whatever has been imported in this interpreter -- and this
    suite runs under ``pytest-randomly`` and ``pytest-xdist``, so a name-based filter would
    make the surface assertion depend on collection order.
    """
    return {
        name: value
        for name, value in _public_attributes(module).items()
        if not isinstance(value, ModuleType)
    }


def test_all_is_sorted_and_is_exactly_the_expected_set() -> None:
    assert rmspec.device.__all__ == sorted(rmspec.device.__all__)
    assert rmspec.device.__all__ == EXPECTED


def test_the_public_surface_is_exactly_what_all_declares() -> None:
    assert sorted(_public_values(rmspec.device)) == EXPECTED


def test_every_exported_name_resolves_to_a_class() -> None:
    """``__all__`` entries that do not exist fail only on ``import *``, which nobody writes."""
    for name in rmspec.device.__all__:
        assert isinstance(getattr(rmspec.device, name), type)


@pytest.mark.parametrize(("name", "methods"), sorted(CLAIMED_METHODS.items()))
def test_every_exported_name_is_what_it_claims(name: str, methods: tuple[str, ...]) -> None:
    bound = getattr(rmspec.device, name)
    public = tuple(sorted(member for member in vars(bound) if not member.startswith("_")))
    assert public == methods


def test_no_private_module_leaks_into_the_public_surface() -> None:
    surfaced = {
        name
        for name, value in _public_attributes(rmspec.device).items()
        if isinstance(value, ModuleType)
    }
    assert surfaced <= PUBLIC_MODULES

    private = {
        name
        for name, value in vars(rmspec.device).items()
        if name.startswith("_") and isinstance(value, ModuleType)
    }
    # Guard the guard: with no private submodules bound the assertion below is vacuous.
    assert private
    assert private.isdisjoint(rmspec.device.__all__)


def test_no_internal_seam_is_re_exported() -> None:
    assert INTERNAL_NAMES.isdisjoint(rmspec.device.__all__)
    assert INTERNAL_NAMES.isdisjoint(_public_values(rmspec.device))


def test_the_doubles_are_not_re_exported_by_the_package() -> None:
    """A production import must not pull an in-memory adapter into a bindable name."""
    assert "testing" not in rmspec.device.__all__
    assert set(EXPECTED_TESTING).isdisjoint(_public_values(rmspec.device))


def test_the_testing_subpackage_has_the_same_kind_of_pinned_surface() -> None:
    assert rmspec.device.testing.__all__ == EXPECTED_TESTING
    assert sorted(_public_values(rmspec.device.testing)) == sorted(EXPECTED_TESTING)
    for name in rmspec.device.testing.__all__:
        assert getattr(rmspec.device.testing, name) is not None
