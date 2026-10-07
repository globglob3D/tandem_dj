"""
Tests of the icon files shipped with the application and of the script that draws them.
"""

import importlib.util
import struct
import zlib

import pytest

from tandem_dj.paths import SOURCE_ROOT, asset_path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICO_HEADER_LENGTH = 6
ICO_ENTRY_LENGTH = 16


@pytest.fixture(scope="module")
def make_icon():
    """
    Load the script that draws the icon, which lives outside the package.
    """
    specification = importlib.util.spec_from_file_location("make_icon", SOURCE_ROOT / "scripts" / "make_icon.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def decode_png(content: bytes) -> list[list[tuple[int, int, int, int]]]:
    """
    Read back the pixels of a PNG file written by the drawing script.

    :param content: Content of an unfiltered 8-bit RGBA PNG file
    :returns: Rows of pixels
    """
    assert content.startswith(PNG_SIGNATURE)
    position, compressed, width = len(PNG_SIGNATURE), b"", 0
    while position < len(content):
        length, kind = struct.unpack_from(">I4s", content, position)
        payload = content[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            width = struct.unpack_from(">I", payload)[0]
        elif kind == b"IDAT":
            compressed += payload
        position += 12 + length
    raw = zlib.decompress(compressed)
    row_length = 1 + 4 * width
    rows = [raw[start + 1 : start + row_length] for start in range(0, len(raw), row_length)]
    return [[tuple(row[offset : offset + 4]) for offset in range(0, len(row), 4)] for row in rows]


def test_shipped_icon_files_are_what_the_script_draws(make_icon):
    """
    The icon files in the package hold, at every size, the picture the drawing script draws now.
    """
    assert decode_png(asset_path("icon.png").read_bytes()) == make_icon.render(make_icon.PNG_SIZE)

    windows_icon = asset_path("icon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", windows_icon)
    assert (reserved, kind, count) == (0, 1, len(make_icon.ICO_SIZES))
    for position, size in enumerate(sorted(make_icon.ICO_SIZES)):
        entry = struct.unpack_from("<BBBBHHII", windows_icon, ICO_HEADER_LENGTH + ICO_ENTRY_LENGTH * position)
        width, height, length, offset = entry[0], entry[1], entry[6], entry[7]
        assert (width, height) == (size % 256, size % 256)
        assert decode_png(windows_icon[offset : offset + length]) == make_icon.render(size)

    mac_icon = asset_path("icon.icns").read_bytes()
    assert mac_icon[:4] == b"icns"
    assert struct.unpack_from(">I", mac_icon, 4)[0] == len(mac_icon)
    position = 8
    for kind, size in make_icon.ICNS_ENTRIES:
        entry_kind, entry_length = struct.unpack_from(">4sI", mac_icon, position)
        assert entry_kind == kind.encode("ascii")
        assert decode_png(mac_icon[position + 8 : position + entry_length]) == make_icon.render(size)
        position += entry_length
    assert position == len(mac_icon)


def test_icon_keeps_square_pixels_and_transparent_corners(make_icon):
    """
    Every size is an exact enlargement of a drawing, and the corners outside the rounded square are see-through.
    """
    for size in make_icon.ICO_SIZES:
        pixels = make_icon.render(size)
        assert len(pixels) == size and all(len(row) == size for row in pixels)
        assert pixels[0][0] == make_icon.TRANSPARENT
        assert pixels[size // 2][0] == make_icon.BORDER
    colors = {pixel for row in make_icon.render(32) for pixel in row}
    assert {make_icon.GREEN, make_icon.BEIGE, make_icon.BACKGROUND} <= colors
