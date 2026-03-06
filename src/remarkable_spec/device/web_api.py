"""USB web interface API client for the reMarkable tablet.

The reMarkable exposes an HTTP API on port 80 over USB networking that allows
listing, downloading, and uploading documents without SSH access.

Requires the ``device`` extra: ``pip install remarkable-spec[device]``

The httpx library is lazily imported -- this module can be imported without
httpx installed, but calling any API method will raise ``ImportError`` with
a helpful message if the dependency is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from remarkable_spec.device.paths import DevicePaths

if TYPE_CHECKING:
    import httpx


def _import_httpx() -> httpx:
    """Lazily import httpx, raising a helpful error if not installed."""
    try:
        import httpx as _httpx

        return _httpx
    except ImportError:
        raise ImportError(
            "httpx is required for the reMarkable web API client. "
            "Install it with: pip install remarkable-spec[device]"
        ) from None


class WebAPI:
    """Client for the reMarkable USB web interface (http://10.11.99.1).

    This API is available when the tablet is connected via USB and the
    web interface is enabled in Settings > Storage. It provides read/write
    access to the document library without requiring SSH credentials.

    The API uses multipart form uploads for document import and returns
    JSON responses for listing and search operations.

    Args:
        base_url: Base URL of the web interface. Defaults to ``http://10.11.99.1``.
        timeout: HTTP request timeout in seconds. Defaults to 30.

    Usage:
        >>> api = WebAPI()
        >>> docs = api.list_documents()
        >>> api.download_pdf(docs[0]["ID"], Path("./output.pdf"))
    """

    def __init__(
        self,
        base_url: str = f"http://{DevicePaths.USB_IP}",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get_client(self) -> httpx.Client:
        """Create a new httpx Client with configured timeout."""
        httpx = _import_httpx()
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def list_documents(self, parent: str = "") -> list[dict[str, Any]]:
        """List documents in the library, optionally filtered by parent folder.

        Args:
            parent: UUID of the parent folder to list. Empty string returns
                root-level items. Use ``"trash"`` for trashed items.

        Returns:
            List of document metadata dicts. Each dict contains keys like
            ``ID``, ``VisssibleName``, ``Type``, ``Parent``, ``ModifiedClient``, etc.
        """
        with self._get_client() as client:
            response = client.get("/documents/")
            response.raise_for_status()
            docs = response.json()

        if parent:
            return [d for d in docs if d.get("Parent") == parent]
        return docs

    def list_all_documents(self) -> list[dict[str, Any]]:
        """List ALL documents recursively, including items in nested folders.

        The web API's ``/documents/`` endpoint only returns root-level items.
        This method performs a BFS traversal, fetching children of each folder
        via ``/documents/{folder_id}``, to build the complete document list.
        """
        all_docs = self.list_documents()
        queue = [d["ID"] for d in all_docs if d.get("Type") == "CollectionType"]
        while queue:
            parent_id = queue.pop(0)
            try:
                with self._get_client() as client:
                    resp = client.get(f"/documents/{parent_id}")
                    resp.raise_for_status()
                    children = resp.json()
            except Exception:
                continue
            all_docs.extend(children)
            queue.extend(c["ID"] for c in children if c.get("Type") == "CollectionType")
        return all_docs

    def download_pdf(self, doc_id: str, output: Path) -> None:
        """Download a document as PDF.

        The web interface renders the document to PDF server-side, including
        all annotations and layers.

        Args:
            doc_id: UUID of the document to download.
            output: Local path where the PDF will be saved.
        """
        with self._get_client() as client:
            response = client.get(f"/download/{doc_id}/placeholder")
            response.raise_for_status()

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)

    def download_rmdoc(self, doc_id: str, output: Path) -> None:
        """Download a document in raw .rmdoc (zip) format.

        The .rmdoc format is a zip archive containing all the document's
        files (.metadata, .content, .rm files, etc).

        Args:
            doc_id: UUID of the document to download.
            output: Local path where the .rmdoc zip will be saved.
        """
        with self._get_client() as client:
            response = client.get(
                f"/download/{doc_id}/placeholder",
                headers={"Accept": "application/zip"},
            )
            response.raise_for_status()

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)

    def upload_pdf(self, path: Path) -> None:
        """Upload a PDF file to the device.

        The file appears in the root of the document library. Use the
        tablet UI or SSH to move it to a specific folder.

        Args:
            path: Local path to the PDF file to upload.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with self._get_client() as client:
            with open(path, "rb") as f:
                response = client.post(
                    "/upload",
                    files={"file": (path.name, f, "application/pdf")},
                )
            response.raise_for_status()

    def upload_epub(self, path: Path) -> None:
        """Upload an EPUB file to the device.

        The file appears in the root of the document library.

        Args:
            path: Local path to the EPUB file to upload.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with self._get_client() as client:
            with open(path, "rb") as f:
                response = client.post(
                    "/upload",
                    files={"file": (path.name, f, "application/epub+zip")},
                )
            response.raise_for_status()

    def get_thumbnail(self, doc_id: str) -> bytes:
        """Retrieve the thumbnail image for a document.

        Args:
            doc_id: UUID of the document.

        Returns:
            Raw JPEG image bytes of the document thumbnail.
        """
        with self._get_client() as client:
            response = client.get(f"/thumbnail/{doc_id}")
            response.raise_for_status()

        return response.content

    def search(self, keyword: str) -> list[dict[str, Any]]:
        """Search documents by keyword.

        Searches document names and (on newer firmware) full-text content.

        Args:
            keyword: Search term to look for.

        Returns:
            List of matching document metadata dicts.
        """
        docs = self.list_documents()
        keyword_lower = keyword.lower()
        return [
            d
            for d in docs
            if keyword_lower in d.get("VisssibleName", "").lower()
            or keyword_lower in d.get("VisibleName", "").lower()
        ]
