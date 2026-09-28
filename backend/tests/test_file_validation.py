"""
Gate D2: magic-byte validation unit tests.

The point of these tests is that the DECISION comes from the bytes, never
from a label: every case here passes only the content (no header, no
extension) to the validator.
"""
import pytest

from app.core.config import get_settings
from app.core.file_validation import UnsupportedMediaError, sniff_mime, validate_media_signature
from media_helpers import avi_bytes, gif_bytes, jpeg_bytes, mp4_bytes, png_bytes, webp_bytes

settings = get_settings()


@pytest.mark.parametrize(
    "make,expected",
    [
        (jpeg_bytes, "image/jpeg"),
        (png_bytes, "image/png"),
        (webp_bytes, "image/webp"),
        (gif_bytes, "image/gif"),
        (mp4_bytes, "video/mp4"),
        (avi_bytes, "video/x-msvideo"),
    ],
)
def test_sniff_detects_real_formats(make, expected):
    assert sniff_mime(make()) == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"just some text",
        b"<html><script>alert(1)</script></html>",
        b"#!/bin/sh\nrm -rf /\n",
        b"MZ\x90\x00\x03\x00\x00\x00",           # Windows PE executable
        b"\x7fELF\x02\x01\x01\x00",              # Linux ELF executable
        b"PK\x03\x04" + b"\x00" * 30,            # zip archive
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",        # PDF
    ],
)
def test_non_media_content_is_rejected_for_both_categories(payload):
    with pytest.raises(UnsupportedMediaError):
        validate_media_signature(payload, settings.ALLOWED_IMAGE_TYPES, "image")
    with pytest.raises(UnsupportedMediaError):
        validate_media_signature(payload, settings.ALLOWED_VIDEO_TYPES, "video")


def test_valid_image_is_accepted_and_returns_detected_mime():
    assert validate_media_signature(png_bytes(), settings.ALLOWED_IMAGE_TYPES, "image") == "image/png"


def test_video_bytes_are_rejected_where_only_images_are_allowed_and_vice_versa():
    with pytest.raises(UnsupportedMediaError):
        validate_media_signature(mp4_bytes(), settings.ALLOWED_IMAGE_TYPES, "image")
    with pytest.raises(UnsupportedMediaError):
        validate_media_signature(jpeg_bytes(), settings.ALLOWED_VIDEO_TYPES, "video")


def test_a_real_but_disallowed_format_is_rejected():
    # A genuine BMP is a recognised image format, but not in the allow-list.
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="BMP")
    assert sniff_mime(buf.getvalue()) == "image/bmp"
    with pytest.raises(UnsupportedMediaError):
        validate_media_signature(buf.getvalue(), settings.ALLOWED_IMAGE_TYPES, "image")


def test_error_message_names_only_the_category():
    with pytest.raises(UnsupportedMediaError) as ei:
        validate_media_signature(b"MZ\x90\x00", settings.ALLOWED_VIDEO_TYPES, "video")
    assert str(ei.value) == "File content does not match a supported video format."
