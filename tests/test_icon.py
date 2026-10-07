"""
Tests of the icon files shipped with the application and of the script that draws them.
"""

import importlib.util
import struct

import pytest

from tandem_dj.paths import SOURCE_ROOT, asset_path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def make_icon():
    """
    Load the script that draws the icon, which lives outside the package.
    """
    specification = importlib.util.spec_from_file_location("make_icon", SOURCE_ROOT / "scripts" / "make_icon.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_shipped_icon_files_are_what_the_script_draws(make_icon):
    """
    The icon files in the package are up to date with the drawing script.
    """
    assert asset_path("icon.png").read_bytes() == make_icon.encode_png(make_icon.render(make_icon.PNG_SIZE))
    assert asset_path("icon.ico").read_bytes() == make_icon.encode_ico(
        {size: make_icon.encode_png(make_icon.render(size)) for size in make_icon.ICO_SIZES}
    )
    assert asset_path("icon.icns").read_bytes() == make_icon.encode_icns(
        [(kind, make_icon.encode_png(make_icon.render(size))) for kind, size in make_icon.ICNS_ENTRIES]
    )


def test_icon_files_have_the_structure_their_formats_require(make_icon):
    """
    The Windows icon lists one picture per size, and the macOS icon declares its own length.
    """
    windows_icon = asset_path("icon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", windows_icon)
    assert (reserved, kind, count) == (0, 1, len(make_icon.ICO_SIZES))
    for position, size in enumerate(sorted(make_icon.ICO_SIZES)):
        width, height, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", windows_icon, 6 + 16 * position)
        assert (width, height) == (size % 256, size % 256)
        assert windows_icon[offset : offset + 8] == PNG_SIGNATURE
        assert offset + length <= len(windows_icon)

    mac_icon = asset_path("icon.icns").read_bytes()
    assert mac_icon[:4] == b"icns"
    assert struct.unpack_from(">I", mac_icon, 4)[0] == len(mac_icon)
    assert asset_path("icon.png").read_bytes().startswith(PNG_SIGNATURE)


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
