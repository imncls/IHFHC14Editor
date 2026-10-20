"""Turn any photo into a game portrait (130x158 PNG, centre-cropped) or a team
logo (square DDS, whole image with transparent margins)."""

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import ValidationError

PORTRAIT_SIZE = (130, 158)
# Team logos: the big one for menus and the small one for the scoreboard.
LOGO_SIZE = 512
SMALL_LOGO_SIZE = 48
LOGO_PREVIEW_SIZE = 96
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
# MPO is how many phones save their JPEG photos.
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "BMP", "GIF"}
NOT_AN_IMAGE = "The file is not a supported image (JPEG, PNG, BMP or GIF)."


def prepare_portrait(path: Path) -> bytes:
    """Read a photo chosen by the user and return the portrait PNG."""
    return portrait_from_bytes(_read_file(path))


def portrait_from_bytes(data: bytes) -> bytes:
    """Decode untrusted image bytes, crop to the portrait ratio and resize."""
    image = _decode(data)
    portrait = ImageOps.fit(
        image, PORTRAIT_SIZE, Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )
    output = io.BytesIO()
    portrait.save(output, "PNG", optimize=True)
    return output.getvalue()


def prepare_logo(path: Path) -> tuple[bytes, bytes]:
    """Read an image chosen by the user and return the (big, small) logo DDS."""
    return logo_from_bytes(_read_file(path))


def logo_from_bytes(data: bytes) -> tuple[bytes, bytes]:
    image = _decode(data)
    return _logo_dds(image, LOGO_SIZE), _logo_dds(image, SMALL_LOGO_SIZE)


def logo_from_dds(data: bytes) -> tuple[bytes, bytes]:
    """Decode an untrusted DDS logo (as in a league package) and write it
    again as the editor writes logos; a logo made by the editor stays as it
    was, anything else is fitted the same way."""
    image = _decode(data, {"DDS"})
    return _logo_dds(image, LOGO_SIZE), _logo_dds(image, SMALL_LOGO_SIZE)


def logo_preview(dds: bytes) -> bytes | None:
    """A small PNG of a logo for the editor window, or None if unreadable."""
    try:
        with Image.open(io.BytesIO(dds)) as image:
            if image.format != "DDS":
                return None
            preview = ImageOps.contain(
                image.convert("RGBA"), (LOGO_PREVIEW_SIZE, LOGO_PREVIEW_SIZE)
            )
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None
    output = io.BytesIO()
    preview.save(output, "PNG")
    return output.getvalue()


def _logo_dds(image: Image.Image, size: int) -> bytes:
    """The whole image centred on a transparent square, as DXT5 DDS."""
    fitted = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2)
    )
    output = io.BytesIO()
    canvas.save(output, "DDS", pixel_format="DXT5")
    return output.getvalue()


def _read_file(path: Path) -> bytes:
    try:
        if not path.is_file():
            raise ValidationError("The image file was not found.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValidationError("The image is larger than 20 MB.")
        return path.read_bytes()
    except OSError:
        raise ValidationError("The image file cannot be read.") from None


def _decode(data: bytes, formats: set[str] = ALLOWED_FORMATS) -> Image.Image:
    """Untrusted image bytes as an upright RGBA image, within the limits."""
    try:
        with warnings.catch_warnings():
            # Pillow only warns between MAX_PIXELS and twice that; refuse both.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            previous_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in formats:
                        raise ValidationError(NOT_AN_IMAGE)
                    image.load()
                    return ImageOps.exif_transpose(image).convert("RGBA")
            finally:
                Image.MAX_IMAGE_PIXELS = previous_limit
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError("The image is too large.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError(NOT_AN_IMAGE) from None
