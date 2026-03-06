"""Sync operations between a reMarkable device and local filesystem.

Provides ``SyncManager`` for pulling documents from and pushing files to
the reMarkable tablet over SSH. Supports both brute-force full sync and
incremental sync with change detection via the local SQLite database.

Requires the ``device`` extra::

    uv add 'remarkable-spec[device]'
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from remarkable_spec.device.connection import DeviceConnection
from remarkable_spec.device.paths import DevicePaths

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from remarkable_spec.sync.db import SyncDB


class SyncManager:
    """Manages sync between a reMarkable device and local filesystem.

    Provides high-level operations for pulling all documents, pulling a
    single document by UUID, and pushing a PDF to the device.

    All methods require a connected ``DeviceConnection`` instance. The
    connection must be established before calling any sync method.

    Usage:
        >>> with DeviceConnection(password="my-password") as conn:
        ...     sync = SyncManager()
        ...     sync.pull_all(Path("./backup"), conn)
        ...     sync.pull_document("abc-123-...", Path("./docs"), conn)
        ...     sync.push_pdf(Path("./paper.pdf"), conn, name="My Paper")
    """

    XOCHITL_DIR: str = DevicePaths.XOCHITL_DATA
    TEMPLATES_DIR: str = DevicePaths.TEMPLATES_BUILTIN
    CONFIG_PATH: str = DevicePaths.CONFIG_FILE

    def pull_all(self, dest: Path, connection: DeviceConnection) -> None:
        """Pull all document files from the device to a local directory.

        Downloads the entire xochitl data directory contents. Each document's
        files (.metadata, .content, .pagedata, .rm files, thumbnails) are
        placed in the destination directory mirroring the device layout.

        This can be used for full device backups.

        Args:
            dest: Local directory to store the pulled files.
                Will be created if it does not exist.
            connection: An active DeviceConnection to the device.

        Raises:
            ConnectionError: If the connection is not active.
        """
        dest.mkdir(parents=True, exist_ok=True)

        # List all files in the xochitl directory
        entries = connection.list_dir(self.XOCHITL_DIR)

        for entry in entries:
            remote_path = f"{self.XOCHITL_DIR}/{entry}"
            local_path = dest / entry

            # Check if entry is a directory (document data dir or thumbnails)
            try:
                sub_entries = connection.list_dir(remote_path)
                # It's a directory -- pull its contents
                local_path.mkdir(parents=True, exist_ok=True)
                for sub_entry in sub_entries:
                    connection.get_file(
                        f"{remote_path}/{sub_entry}",
                        local_path / sub_entry,
                    )
            except OSError:
                # It's a file -- pull directly
                connection.get_file(remote_path, local_path)

    def pull_document(self, doc_uuid: str, dest: Path, connection: DeviceConnection) -> None:
        """Pull a single document and all its associated files from the device.

        Downloads the document's metadata, content, pagedata, page data
        directory, and thumbnails directory.

        Args:
            doc_uuid: UUID string of the document to pull.
            dest: Local directory to store the pulled files.
            connection: An active DeviceConnection to the device.

        Raises:
            ConnectionError: If the connection is not active.
            FileNotFoundError: If the document does not exist on the device.
        """
        dest.mkdir(parents=True, exist_ok=True)

        # Document-associated file extensions and directories
        extensions = [".metadata", ".content", ".pagedata", ".pdf", ".epub"]

        for ext in extensions:
            remote_path = f"{self.XOCHITL_DIR}/{doc_uuid}{ext}"
            local_path = dest / f"{doc_uuid}{ext}"
            try:
                connection.get_file(remote_path, local_path)
            except (OSError, FileNotFoundError):
                # Not all extensions exist for every document
                continue

        # Pull the page data directory ({UUID}/)
        page_dir = f"{self.XOCHITL_DIR}/{doc_uuid}"
        local_page_dir = dest / doc_uuid
        try:
            page_files = connection.list_dir(page_dir)
            local_page_dir.mkdir(parents=True, exist_ok=True)
            for page_file in page_files:
                connection.get_file(
                    f"{page_dir}/{page_file}",
                    local_page_dir / page_file,
                )
        except OSError:
            pass

        # Pull the thumbnails directory ({UUID}.thumbnails/)
        thumb_dir = f"{self.XOCHITL_DIR}/{doc_uuid}.thumbnails"
        local_thumb_dir = dest / f"{doc_uuid}.thumbnails"
        try:
            thumb_files = connection.list_dir(thumb_dir)
            local_thumb_dir.mkdir(parents=True, exist_ok=True)
            for thumb_file in thumb_files:
                connection.get_file(
                    f"{thumb_dir}/{thumb_file}",
                    local_thumb_dir / thumb_file,
                )
        except OSError:
            pass

    def push_pdf(
        self,
        pdf_path: Path,
        connection: DeviceConnection,
        name: str | None = None,
    ) -> None:
        """Push a PDF file to the device as a new document.

        Creates the necessary metadata files (.metadata, .content, .pagedata)
        and uploads the PDF. The document appears in the root of the library
        after restarting xochitl.

        Args:
            pdf_path: Local path to the PDF file.
            connection: An active DeviceConnection to the device.
            name: Display name for the document. Defaults to the PDF filename
                without extension.

        Raises:
            ConnectionError: If the connection is not active.
            FileNotFoundError: If the PDF file does not exist.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        doc_name = name or pdf_path.stem
        doc_uuid = str(uuid.uuid4())
        remote_base = f"{self.XOCHITL_DIR}/{doc_uuid}"

        # Create .metadata
        metadata = {
            "deleted": False,
            "lastModified": "",
            "lastOpened": "",
            "lastOpenedPage": 0,
            "metadatamodified": False,
            "modified": False,
            "parent": "",
            "pinned": False,
            "synced": False,
            "type": "DocumentType",
            "version": 0,
            "visibleName": doc_name,
        }

        # Create .content
        content = {
            "fileType": "pdf",
            "formatVersion": 2,
            "orientation": "portrait",
            "pageCount": 0,
            "pages": [],
        }

        # Upload the PDF
        connection.put_file(pdf_path, f"{remote_base}.pdf")

        # Upload metadata and content as JSON
        # Write temp files for upload
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".metadata", delete=False) as f:
            json.dump(metadata, f)
            meta_tmp = Path(f.name)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".content", delete=False) as f:
            json.dump(content, f)
            content_tmp = Path(f.name)

        try:
            connection.put_file(meta_tmp, f"{remote_base}.metadata")
            connection.put_file(content_tmp, f"{remote_base}.content")
        finally:
            meta_tmp.unlink(missing_ok=True)
            content_tmp.unlink(missing_ok=True)

        # Create the page data directory
        connection.execute(f"mkdir -p {remote_base}")

        # Restart xochitl to pick up the new document
        connection.execute("systemctl restart xochitl")

    # ── Incremental Sync ───────────────────────────────────────────

    def sync_status(
        self,
        connection: DeviceConnection,
        db: SyncDB,
        local_dir: Path,
    ) -> list[tuple[str, str, str]]:
        """Compare device state vs local DB to find what's changed.

        Fetches metadata from the device, compares ``lastModified``
        timestamps against the sync database, and returns a list of
        changes.

        Args:
            connection: An active DeviceConnection.
            db: SyncDB instance for change detection.
            local_dir: Local xochitl mirror directory.

        Returns:
            List of ``(doc_uuid, visible_name, change_type)`` tuples where
            change_type is one of ``new_on_device``, ``modified_on_device``,
            ``deleted_local``.
        """
        import tempfile

        changes: list[tuple[str, str, str]] = []

        # List all .metadata files on device
        entries = connection.list_dir(self.XOCHITL_DIR)
        device_uuids: set[str] = set()

        for entry in entries:
            if not entry.endswith(".metadata"):
                continue
            doc_uuid = entry.removesuffix(".metadata")
            device_uuids.add(doc_uuid)

            # Fetch metadata to temp file
            remote_path = f"{self.XOCHITL_DIR}/{entry}"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                connection.get_file(remote_path, tmp_path)
                meta = json.loads(tmp_path.read_text())
            except Exception:
                continue
            finally:
                tmp_path.unlink(missing_ok=True)

            name = meta.get("visibleName", doc_uuid[:8])
            last_mod_str = meta.get("lastModified", "0")
            try:
                device_last_modified = int(last_mod_str) if last_mod_str else 0
            except (ValueError, TypeError):
                device_last_modified = 0

            tracked = db.get_document(doc_uuid)
            if tracked is None:
                changes.append((doc_uuid, name, "new_on_device"))
            elif device_last_modified > tracked.device_last_modified:
                changes.append((doc_uuid, name, "modified_on_device"))

        # Check for documents deleted on device
        for tracked_doc in db.list_documents():
            if tracked_doc.doc_uuid not in device_uuids:
                changes.append(
                    (tracked_doc.doc_uuid, tracked_doc.visible_name, "deleted_on_device")
                )

        return changes

    def sync_pull(
        self,
        dest: Path,
        connection: DeviceConnection,
        db: SyncDB,
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, str]]]:
        """Incremental pull: only download documents that changed on device.

        Uses the sync database for change detection. Only transfers
        documents where the device's ``lastModified`` timestamp is newer
        than what's recorded locally.

        Args:
            dest: Local xochitl mirror directory.
            connection: An active DeviceConnection.
            db: SyncDB instance.

        Returns:
            Tuple of (pulled, skipped) where pulled is a list of
            ``(doc_uuid, visible_name, pages_pulled)`` and skipped is a
            list of ``(doc_uuid, visible_name, error_message)``.
        """
        from remarkable_spec.sync.hasher import hash_document_files, hash_file
        from remarkable_spec.sync.models import SyncDocument, SyncLogEntry, SyncPage

        changes = self.sync_status(connection, db, dest)
        pulled: list[tuple[str, str, int]] = []
        skipped: list[tuple[str, str, str]] = []

        for doc_uuid, name, change_type in changes:
            if change_type == "deleted_on_device":
                db.delete_document(doc_uuid)
                continue

            try:
                # Pull the document
                self.pull_document(doc_uuid, dest, connection)

                # Compute hashes and update DB
                hashes = hash_document_files(dest, doc_uuid)
                meta_path = dest / f"{doc_uuid}.metadata"
                content_path = dest / f"{doc_uuid}.content"

                # Parse metadata for DB record
                meta = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                    except (json.JSONDecodeError, ValueError):
                        _log.warning("Invalid metadata JSON for %s, using defaults", doc_uuid[:8])

                content = {}
                page_uuids: list[str] = []
                if content_path.exists():
                    try:
                        content = json.loads(content_path.read_text())
                    except (json.JSONDecodeError, ValueError):
                        _log.warning("Invalid content JSON for %s, using defaults", doc_uuid[:8])
                    if "cPages" in content and "pages" in content["cPages"]:
                        page_uuids = [p["id"] for p in content["cPages"]["pages"]]
                    elif "pages" in content:
                        page_uuids = content["pages"]

                last_mod_str = meta.get("lastModified", "0")
                try:
                    device_last_modified = int(last_mod_str) if last_mod_str else 0
                except (ValueError, TypeError):
                    device_last_modified = 0

                doc = SyncDocument(
                    doc_uuid=doc_uuid,
                    visible_name=meta.get("visibleName", name),
                    doc_type=meta.get("type", "DocumentType"),
                    file_type=content.get("fileType", "notebook"),
                    parent=meta.get("parent", ""),
                    page_count=len(page_uuids),
                    metadata_hash=hashes.get("metadata"),
                    content_hash=hashes.get("content"),
                    device_last_modified=device_last_modified,
                    local_path=str(dest),
                )
                db.upsert_document(doc)

                # Update per-page tracking
                pages_pulled = 0
                for idx, page_uuid in enumerate(page_uuids):
                    rm_path = dest / doc_uuid / f"{page_uuid}.rm"
                    rm_hash = None
                    rm_size = None
                    if rm_path.exists():
                        rm_hash = hash_file(rm_path)
                        rm_size = rm_path.stat().st_size
                        pages_pulled += 1

                    db.upsert_page(
                        SyncPage(
                            page_uuid=page_uuid,
                            doc_uuid=doc_uuid,
                            page_index=idx,
                            rm_hash=rm_hash,
                            rm_size_bytes=rm_size,
                        )
                    )

                # Log the sync
                db.log_sync(
                    SyncLogEntry(
                        direction="pull",
                        doc_uuid=doc_uuid,
                        doc_name=doc.visible_name,
                        pages_transferred=pages_pulled,
                        status="ok",
                        details=change_type,
                    )
                )

                pulled.append((doc_uuid, doc.visible_name, pages_pulled))
            except Exception as exc:
                _log.warning("Skipping %s (%s): %s", name, doc_uuid[:8], exc)
                with contextlib.suppress(Exception):
                    db.log_sync(
                        SyncLogEntry(
                            direction="pull",
                            doc_uuid=doc_uuid,
                            doc_name=name,
                            status="error",
                            details=str(exc),
                        )
                    )
                skipped.append((doc_uuid, name, str(exc)))
                continue

        return pulled, skipped

    @staticmethod
    def _count_pdf_pages(file_path: Path) -> int:
        """Count pages in a PDF. Uses pymupdf for accuracy, falls back to regex."""
        try:
            import pymupdf

            doc = pymupdf.open(str(file_path))
            try:
                return len(doc)
            finally:
                doc.close()
        except Exception:
            # Fallback: scan for /Type /Page entries
            data = file_path.read_bytes()
            import re

            matches = re.findall(rb"/Type\s*/Page[^s]", data)
            return max(len(matches), 1)

    def sync_push_file(
        self,
        file_path: Path,
        connection: DeviceConnection,
        db: SyncDB,
        name: str | None = None,
        parent: str = "",
    ) -> str:
        """Push a file to the device and record in sync database.

        Supports PDF and EPUB files directly. For other file types, use
        ``render_to_pdf()`` from ``remarkable_spec.device.push`` first.

        Args:
            file_path: Local file to push (must be .pdf or .epub).
            connection: An active DeviceConnection.
            db: SyncDB instance.
            name: Display name on device. Defaults to file stem.
            parent: Parent folder UUID (empty = root).

        Returns:
            UUID assigned to the new document on device.
        """
        from remarkable_spec.sync.models import SyncDocument, SyncLogEntry

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in (".pdf", ".epub"):
            raise ValueError(f"Unsupported file type for push: {suffix}")

        doc_name = name or file_path.stem
        doc_uuid = str(uuid.uuid4())
        remote_base = f"{self.XOCHITL_DIR}/{doc_uuid}"

        file_type = "pdf" if suffix == ".pdf" else "epub"

        # Read page count from PDF for proper device indexing
        page_count = 0
        if file_type == "pdf":
            page_count = self._count_pdf_pages(file_path)

        metadata = {
            "deleted": False,
            "lastModified": str(int(time.time() * 1000)),
            "lastOpened": "",
            "lastOpenedPage": 0,
            "metadatamodified": True,
            "modified": True,
            "parent": parent,
            "pinned": False,
            "synced": False,
            "type": "DocumentType",
            "version": 0,
            "visibleName": doc_name,
        }

        # Generate page UUIDs so xochitl can map PDF pages to .rm overlay files
        page_uuids = [str(uuid.uuid4()) for _ in range(page_count)]

        content = {
            "fileType": file_type,
            "formatVersion": 2,
            "orientation": "portrait",
            "pageCount": page_count,
            "pages": page_uuids,
        }

        # Upload file
        connection.put_file(file_path, f"{remote_base}{suffix}")

        # Create empty .rm stubs for each page so xochitl doesn't complain
        # about missing page files
        connection.execute(f"mkdir -p {remote_base}")
        for page_uuid_str in page_uuids:
            connection.execute(f"touch {remote_base}/{page_uuid_str}.rm")

        # Upload metadata and content
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".metadata", delete=False) as f:
            json.dump(metadata, f)
            meta_tmp = Path(f.name)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".content", delete=False) as f:
            json.dump(content, f)
            content_tmp = Path(f.name)

        try:
            connection.put_file(meta_tmp, f"{remote_base}.metadata")
            connection.put_file(content_tmp, f"{remote_base}.content")
        finally:
            meta_tmp.unlink(missing_ok=True)
            content_tmp.unlink(missing_ok=True)

        connection.execute("systemctl restart xochitl")

        # Record in sync DB
        db.upsert_document(
            SyncDocument(
                doc_uuid=doc_uuid,
                visible_name=doc_name,
                file_type=file_type,
                parent=parent,
            )
        )
        db.log_sync(
            SyncLogEntry(
                direction="push",
                doc_uuid=doc_uuid,
                doc_name=doc_name,
                status="ok",
                details=f"Pushed {file_path.name}",
            )
        )

        return doc_uuid
