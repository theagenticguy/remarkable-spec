"""Color system for reMarkable tablets.

The reMarkable uses integer color IDs stored per-stroke in .rm files.
The Paper Pro extends the palette to 14 colors (IDs 0-13).

Color export RGB values differ from the physical on-screen appearance
because the e-ink display renders colors much more muted than LCD/OLED.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class PenColor(enum.IntEnum):
    """Color index stored per stroke in .rm binary files.

    Values 0-2 are available on all reMarkable devices (rM1, rM2).
    Values 3-7 were introduced with the Paper Pro's color e-ink display.
    Values 8-13 are extended palette colors (Paper Pro only).

    The color ID is written as a uint32 in the .rm binary format and maps
    to a specific RGB triplet when exported to PNG/SVG/PDF.
    """

    BLACK = 0
    GRAY = 1
    WHITE = 2
    YELLOW = 3
    GREEN = 4
    PINK = 5
    BLUE = 6
    RED = 7
    GRAY_OVERLAP = 8
    HIGHLIGHT = 9  # Shared ID; actual color from extra block data or extraMetadata
    GREEN_2 = 10
    CYAN = 11
    MAGENTA = 12
    YELLOW_2 = 13


class HighlightColor(enum.Enum):
    """Highlight colors that share PenColor.HIGHLIGHT (ID 9).

    The actual highlight color is determined by extra data in the v6 scene
    block or from the extraMetadata field in the .content JSON file.
    These string values match the keys used in reMarkable's internal config.
    """

    YELLOW = "HighlighterYellow"
    GREEN = "HighlighterGreen"
    PINK = "HighlighterPink"
    BLUE = "HighlighterBlue"
    ORANGE = "HighlighterOrange"


class RGB(BaseModel):
    """8-bit RGB color value used for color palette definitions.

    Represents a single color in the standard 0-255 range per channel.
    Used both for export palette colors (what gets rendered to files) and
    physical display colors (what the e-ink screen actually shows).
    """

    model_config = ConfigDict(frozen=True)

    r: int = Field(description="Red channel value (0-255).")
    g: int = Field(description="Green channel value (0-255).")
    b: int = Field(description="Blue channel value (0-255).")

    def as_tuple(self) -> tuple[int, int, int]:
        """Return the color as an (R, G, B) tuple."""
        return (self.r, self.g, self.b)

    def as_hex(self) -> str:
        """Return the color as a lowercase hex string, e.g. '#ff8040'."""
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def as_css(self) -> str:
        """Return the color as a CSS rgb() function string."""
        return f"rgb({self.r}, {self.g}, {self.b})"


# Standard export palette -- RGB values used when rendering to PNG/SVG/PDF.
# These are the "canonical" colors that community tools agree on for export.
# Source: rmc/exporters/writing_tools.py (MIT license)
RM_PALETTE: dict[PenColor, RGB] = {
    PenColor.BLACK: RGB(r=0, g=0, b=0),
    PenColor.GRAY: RGB(r=144, g=144, b=144),
    PenColor.WHITE: RGB(r=255, g=255, b=255),
    PenColor.YELLOW: RGB(r=251, g=247, b=25),
    PenColor.GREEN: RGB(r=0, g=255, b=0),
    PenColor.PINK: RGB(r=255, g=192, b=203),
    PenColor.BLUE: RGB(r=78, g=105, b=201),
    PenColor.RED: RGB(r=179, g=62, b=57),
    PenColor.GRAY_OVERLAP: RGB(r=125, g=125, b=125),
    PenColor.GREEN_2: RGB(r=161, g=216, b=125),
    PenColor.CYAN: RGB(r=139, g=208, b=229),
    PenColor.MAGENTA: RGB(r=183, g=130, b=205),
    PenColor.YELLOW_2: RGB(r=247, g=232, b=81),
}

# Physical on-screen colors measured from the Paper Pro display via DSLR calibration.
# These represent what the e-ink screen actually shows, which is much more muted
# than the export palette due to the limited color gamut of color e-ink.
# Source: thregr.org/wavexx (ICC profile rmpro-v0.icc)
PAPER_PRO_PHYSICAL: dict[PenColor, RGB] = {
    PenColor.BLACK: RGB(r=0x3A, g=0x48, b=0x61),
    PenColor.GRAY: RGB(r=0x7F, g=0x7E, b=0x82),
    PenColor.WHITE: RGB(r=0xA8, g=0xAA, b=0xA7),
    PenColor.BLUE: RGB(r=0x3C, g=0x54, b=0x83),
    PenColor.RED: RGB(r=0x86, g=0x63, b=0x69),
    PenColor.GREEN: RGB(r=0x6E, g=0x78, b=0x60),
    PenColor.YELLOW: RGB(r=0xA0, g=0x9E, b=0x66),
    PenColor.CYAN: RGB(r=0x5F, g=0x6D, b=0x80),
    PenColor.MAGENTA: RGB(r=0x7F, g=0x62, b=0x7B),
}
