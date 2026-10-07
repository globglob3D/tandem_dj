"""
Draw the icon of the application: two birds facing each other in pixel art, in the colours of the window.

The bird is drawn by hand as a small pixel map, placed twice on a grid (once flipped) and enlarged without smoothing,
which keeps the pixels square at every size. A coarser drawing serves the sizes the detailed one does not divide.
Running this script rewrites ``icon.png``, ``icon.ico`` (Windows) and ``icon.icns`` (macOS) in the assets folder of
the package::

    uv run python scripts/make_icon.py
"""

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

ASSETS_DIRECTORY = Path(__file__).resolve().parents[1] / "src" / "tandem_dj" / "assets"

Color = tuple[int, int, int, int]
TRANSPARENT: Color = (0, 0, 0, 0)
BACKGROUND: Color = (0x20, 0x1D, 0x18, 255)
BORDER: Color = (0x4A, 0x43, 0x37, 255)
GREEN: Color = (0x9C, 0xB8, 0x7C, 255)
GREEN_SHADE: Color = (0x6F, 0x88, 0x59, 255)
BEIGE: Color = (0xD5, 0xC8, 0xAB, 255)
BEIGE_SHADE: Color = (0x8D, 0x84, 0x70, 255)
AMBER: Color = (0xD9, 0xA5, 0x4C, 255)

PNG_SIZE = 256
ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICNS_ENTRIES = (("ic11", 32), ("ic12", 64), ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512))

BODY_CELL = "#"
WING_CELL = "w"
EYE_CELL = "o"
BEAK_CELL = "b"
LEG_CELL = "l"

LARGE_BIRD = (
    "......####...",
    ".....######..",
    "....####o###.",
    "....#######bb",
    "....#######b.",
    "...#########.",
    "..##########.",
    "..#www######.",
    ".##wwww#####.",
    ".#wwwww#####.",
    "##wwwww####..",
    "#wwwww#####..",
    "#wwww#####...",
    "...######....",
    ".....l.l.....",
    ".....l.l.....",
)
SMALL_BIRD = (
    ".###.",
    ".#o#b",
    "####.",
    "#ww#.",
    "#w##.",
    ".##..",
    ".l...",
)


def main() -> None:
    """
    Write the icon files into the assets folder of the package.
    """
    ASSETS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIRECTORY / "icon.png").write_bytes(encode_png(render(PNG_SIZE)))
    (ASSETS_DIRECTORY / "icon.ico").write_bytes(encode_ico({size: encode_png(render(size)) for size in ICO_SIZES}))
    (ASSETS_DIRECTORY / "icon.icns").write_bytes(
        encode_icns([(kind, encode_png(render(size))) for kind, size in ICNS_ENTRIES])
    )
    print(f"Icon written to {ASSETS_DIRECTORY}")


@dataclass(frozen=True)
class Bird:
    """
    One bird placed on the drawing grid.

    :param cells: Pixel map of the bird looking to the right, one text per row: ``#`` for the body, ``w`` for the
        wing, ``o`` for the eye, ``b`` for the beak, ``l`` for the legs, anything else for nothing
    :param left: Column of the grid receiving the first column of the pixel map
    :param top: Row of the grid receiving the first row of the pixel map
    :param color: Colour of the body
    :param wing_color: Colour of the wing
    :param looks_left: Whether the pixel map is flipped, so that the bird looks to the left
    """

    cells: tuple[str, ...]
    left: int
    top: int
    color: Color
    wing_color: Color
    looks_left: bool = False

    def color_at(self, column: int, row: int) -> Color | None:
        """
        Tell what the bird paints in a cell of the grid.

        :param column: Column of the cell
        :param row: Row of the cell
        :returns: The colour of the part of the bird in that cell, ``None`` where the bird paints nothing
        """
        bird_column, bird_row = column - self.left, row - self.top
        if not (0 <= bird_row < len(self.cells) and 0 <= bird_column < len(self.cells[bird_row])):
            return None
        if self.looks_left:
            bird_column = len(self.cells[bird_row]) - 1 - bird_column
        colors = {
            BODY_CELL: self.color,
            WING_CELL: self.wing_color,
            EYE_CELL: BACKGROUND,
            BEAK_CELL: AMBER,
            LEG_CELL: AMBER,
        }
        return colors.get(self.cells[bird_row][bird_column])


@dataclass(frozen=True)
class Drawing:
    """
    The icon at one level of detail.

    :param grid: Number of cells per side
    :param corner_radius: Radius of the rounded corners of the background, in cells
    :param birds: The birds, the first one drawn on top
    """

    grid: int
    corner_radius: float
    birds: tuple[Bird, ...]


DETAILED_DRAWING = Drawing(
    grid=32,
    corner_radius=5.0,
    birds=(
        Bird(LARGE_BIRD, 2, 8, GREEN, GREEN_SHADE),
        Bird(LARGE_BIRD, 17, 8, BEIGE, BEIGE_SHADE, looks_left=True),
    ),
)
SMALL_DRAWING = Drawing(
    grid=16,
    corner_radius=2.5,
    birds=(
        Bird(SMALL_BIRD, 2, 4, GREEN, GREEN_SHADE),
        Bird(SMALL_BIRD, 9, 4, BEIGE, BEIGE_SHADE, looks_left=True),
    ),
)


def render(size: int) -> list[list[Color]]:
    """
    Draw the icon at a given size.

    Sizes that are a multiple of the detailed grid enlarge the detailed drawing; the others enlarge the coarser
    drawing, so that pixels stay square.

    :param size: Width and height of the picture, in pixels
    :returns: Rows of pixels
    """
    drawing = DETAILED_DRAWING if size % DETAILED_DRAWING.grid == 0 else SMALL_DRAWING
    cells = draw_cells(drawing)
    scale = size // drawing.grid
    return [[cells[row // scale][column // scale] for column in range(size)] for row in range(size)]


def draw_cells(drawing: Drawing) -> list[list[Color]]:
    """
    Compute the colour of every cell of a drawing.

    :param drawing: Drawing to compute
    :returns: Rows of cells
    """
    return [[_cell_color(drawing, column, row) for column in range(drawing.grid)] for row in range(drawing.grid)]


def encode_png(pixels: list[list[Color]]) -> bytes:
    """
    Encode pixels as a PNG file with transparency.

    :param pixels: Rows of pixels
    :returns: Content of the PNG file
    """
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in pixels)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        """
        Wrap a payload as a PNG chunk.

        :param kind: Four letter chunk type
        :param payload: Content of the chunk
        :returns: The chunk with its length and checksum
        """
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(pictures: dict[int, bytes]) -> bytes:
    """
    Bundle PNG pictures of several sizes as a Windows icon file.

    :param pictures: PNG content keyed by picture size in pixels
    :returns: Content of the ICO file
    """
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = len(header) + 16 * len(pictures)
    entries, contents = b"", b""
    for size, picture in sorted(pictures.items()):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(picture), offset)
        contents += picture
        offset += len(picture)
    return header + entries + contents


def encode_icns(pictures: list[tuple[str, bytes]]) -> bytes:
    """
    Bundle PNG pictures as a macOS icon file.

    :param pictures: Icon type code and PNG content of each picture
    :returns: Content of the ICNS file
    """
    body = b"".join(kind.encode("ascii") + struct.pack(">I", len(picture) + 8) + picture for kind, picture in pictures)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def _cell_color(drawing: Drawing, column: int, row: int) -> Color:
    """
    Choose the colour of a cell of a drawing.

    :param drawing: Drawing the cell belongs to
    :param column: Column of the cell
    :param row: Row of the cell
    :returns: The colour of a bird, of the border or of the background; transparent outside the rounded square
    """
    edge_distance = _distance_inside_rounded_square(drawing, column + 0.5, row + 0.5)
    if edge_distance < 0:
        return TRANSPARENT
    if edge_distance < 1:
        return BORDER
    for bird in drawing.birds:
        color = bird.color_at(column, row)
        if color is not None:
            return color
    return BACKGROUND


def _distance_inside_rounded_square(drawing: Drawing, x: float, y: float) -> float:
    """
    Measure how far inside the rounded square of the icon a point is.

    :param drawing: Drawing giving the size of the square and of its corners
    :param x: Horizontal position of the point
    :param y: Vertical position of the point
    :returns: Distance to the edge, negative outside the square
    """
    half, radius = drawing.grid / 2, drawing.corner_radius
    distance_x, distance_y = abs(x - half) - (half - radius), abs(y - half) - (half - radius)
    outside = math.hypot(max(distance_x, 0), max(distance_y, 0))
    return radius - outside - min(max(distance_x, distance_y), 0)


if __name__ == "__main__":
    main()
