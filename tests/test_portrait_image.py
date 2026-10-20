import io
from pathlib import Path

import pytest
from PIL import Image

from app import portrait_image
from app.errors import ValidationError
from app.portrait_image import (
    LOGO_PREVIEW_SIZE,
    LOGO_SIZE,
    PORTRAIT_SIZE,
    SMALL_LOGO_SIZE,
    logo_from_bytes,
    logo_preview,
    portrait_from_bytes,
    prepare_logo,
    prepare_portrait,
)


def encoded(image: Image.Image, image_format: str, **options) -> bytes:
    output = io.BytesIO()
    image.save(output, image_format, **options)
    return output.getvalue()


def decoded(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    return image


@pytest.mark.parametrize("size", [(800, 600), (300, 900), (40, 40)])
def test_any_photo_becomes_a_portrait(size: tuple[int, int]) -> None:
    photo = encoded(Image.new("RGB", size, "red"), "JPEG")
    portrait = decoded(portrait_from_bytes(photo))
    assert portrait.size == PORTRAIT_SIZE
    assert portrait.mode == "RGBA"


def test_centre_crop_keeps_the_middle() -> None:
    # Left third blue, middle red, right third blue: only red should remain.
    photo = Image.new("RGB", (900, 300), "blue")
    photo.paste("red", (300, 0, 600, 300))
    portrait = decoded(portrait_from_bytes(encoded(photo, "PNG")))
    assert portrait.getpixel((0, 79))[:3] == (255, 0, 0)
    assert portrait.getpixel((129, 79))[:3] == (255, 0, 0)


def test_exif_orientation_is_applied() -> None:
    # 400x200 stored sideways (orientation 6 = rotate 90 degrees to show).
    photo = Image.new("RGB", (400, 200), "white")
    photo.paste("black", (0, 0, 200, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    portrait = decoded(portrait_from_bytes(encoded(photo, "JPEG", exif=exif)))
    # Upright, the black half is at the top.
    assert sum(portrait.getpixel((65, 5))[:3]) < 100
    assert sum(portrait.getpixel((65, 150))[:3]) > 600


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\nbroken"])
def test_non_images_are_rejected(data: bytes) -> None:
    with pytest.raises(ValidationError):
        portrait_from_bytes(data)


def test_other_formats_are_rejected() -> None:
    with pytest.raises(ValidationError):
        portrait_from_bytes(encoded(Image.new("RGB", (10, 10)), "TIFF"))


def test_pixel_bombs_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portrait_image, "MAX_PIXELS", 10_000)
    with pytest.raises(ValidationError):
        portrait_from_bytes(encoded(Image.new("RGB", (200, 200)), "PNG"))


def test_file_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        prepare_portrait(tmp_path / "missing.jpg")
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * 20)
    monkeypatch.setattr(portrait_image, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValidationError):
        prepare_portrait(big)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(encoded(Image.new("RGB", (300, 300), "green"), "JPEG"))
    monkeypatch.setattr(portrait_image, "MAX_FILE_BYTES", 20 * 1024 * 1024)
    assert decoded(prepare_portrait(photo)).size == PORTRAIT_SIZE


def test_logo_keeps_the_whole_image_with_transparent_margins() -> None:
    wide = encoded(Image.new("RGB", (400, 200), "red"), "PNG")
    big, small = logo_from_bytes(wide)
    for dds, size in ((big, LOGO_SIZE), (small, SMALL_LOGO_SIZE)):
        assert dds[:4] == b"DDS " and dds[84:88] == b"DXT5"
        image = Image.open(io.BytesIO(dds))
        assert image.size == (size, size)
        rgba = image.convert("RGBA")
        assert rgba.getpixel((size // 2, size // 2))[3] == 255  # the logo
        assert rgba.getpixel((size // 2, 0))[3] == 0  # the margin


def test_logo_preview() -> None:
    big, _small = logo_from_bytes(encoded(Image.new("RGB", (50, 50), "red"), "PNG"))
    preview = decoded(logo_preview(big))
    assert preview.size == (LOGO_PREVIEW_SIZE, LOGO_PREVIEW_SIZE)
    assert logo_preview(b"not a dds") is None
    assert logo_preview(encoded(Image.new("RGB", (5, 5)), "PNG")) is None


def test_logo_refuses_files_that_are_not_images(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValidationError):
        prepare_logo(path)
