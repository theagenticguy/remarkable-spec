"""Well-known filesystem paths on the reMarkable device.

This module centralizes all known filesystem paths, network addresses,
and port numbers for the reMarkable tablet. These constants are used by
the connection, sync, and web API modules.

Paths are valid for reMarkable 2 and Paper Pro running firmware 2.x and 3.x.
"""

from __future__ import annotations


class DevicePaths:
    """Well-known filesystem paths and network constants for the reMarkable device.

    All paths are absolute filesystem paths on the tablet (Linux).
    Network constants reflect the default USB networking configuration.

    Attributes:
        XOCHITL_DATA: Main data directory where all documents are stored.
            Each document is a set of files: {UUID}.metadata, {UUID}.content,
            {UUID}.pagedata, and a {UUID}/ directory with per-page .rm files.
        TEMPLATES_BUILTIN: System templates directory (overwritten on OS upgrades).
        TEMPLATES_CUSTOM: User-writable templates directory (persists across upgrades).
        CONFIG_FILE: Main xochitl configuration file.
        UPDATE_CONF: OS update configuration (contains firmware version info).
        SPLASH_DIR: Directory containing splash screen images (suspended.png, etc).
        SSH_KEYS: Authorized SSH keys file for root access.
        USB_IP: Default IP address of the tablet over USB networking.
        USB_SUBNET: USB network subnet.
        WEB_API_PORT: HTTP port for the USB web interface.
        SSH_PORT: SSH port for shell access.
    """

    XOCHITL_DATA: str = "/home/root/.local/share/remarkable/xochitl"
    TEMPLATES_BUILTIN: str = "/usr/share/remarkable/templates"
    TEMPLATES_CUSTOM: str = "/home/root/.local/share/remarkable/templates"
    CONFIG_FILE: str = "/home/root/.config/remarkable/xochitl.conf"
    UPDATE_CONF: str = "/usr/share/remarkable/update.conf"
    SPLASH_DIR: str = "/usr/share/remarkable"
    SSH_KEYS: str = "/home/root/.ssh/authorized_keys"

    USB_IP: str = "10.11.99.1"
    USB_SUBNET: str = "10.11.99.0/29"
    WEB_API_PORT: int = 80
    SSH_PORT: int = 22
