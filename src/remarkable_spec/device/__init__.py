"""Device access module for the reMarkable tablet.

This module provides:
  - **DeviceConnection** (``connection``) — SSH/SFTP access via paramiko.
  - **WebAPI** (``web_api``) — HTTP client for the USB web interface.
  - **SyncManager** (``sync``) — high-level sync operations.
  - **DevicePaths** (``paths``) — well-known filesystem paths and constants.

All network operations require the ``device`` extra:
``pip install remarkable-spec[device]``
"""

from __future__ import annotations

from remarkable_spec.device.connection import DeviceConnection
from remarkable_spec.device.paths import DevicePaths
from remarkable_spec.device.sync import SyncManager
from remarkable_spec.device.web_api import WebAPI

__all__ = [
    "DeviceConnection",
    "DevicePaths",
    "SyncManager",
    "WebAPI",
]
