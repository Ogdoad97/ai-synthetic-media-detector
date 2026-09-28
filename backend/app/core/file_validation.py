"""
Magic-byte (file signature) validation for uploaded and remotely-fetched
media.

Gate D2 finding: every ingestion path (image upload, video upload, and
remote URL fetch) previously trusted an untrusted-supplied *label* to
decide what kind of file it was looking at - the client's `Content-Type`
header on a multipart upload, or the file extension in a URL's path.
Neither is a security boundary: a client can set any Content-Type it
likes, and a URL's path is just text chosen by whoever controls that
URL. Both were used to route content into `analyze_image` / `analyze_video`
(or, for the video upload path, straight to `cv2.VideoCapture`) with no
check that the bytes actually are what the label claims.

This module inspects the real leading bytes of the content instead - the
same technique `file(1)` and browsers use - and is the only source of
truth for "is this really an image/video, and which kind." It never
raises on the caller's behalf for oversized or corrupt-but-correctly-typed
content; that stays the job of the existing decompression-bomb guard and
the analyzer's own decode error handling.
"""
from __future__ import annotations

from typing import Optional, Set

import filetype


class UnsupportedMediaError(ValueError):
    """
    Raised when content's real file signature doesn't match one of the
    allowed types for its category. The message is safe to return to a
    client as-is: it names no internal path, exception type, or stack
    detail, only the category that was expected.
    """


def sniff_mime(data: bytes) -> Optional[str]:
    """
    Return the MIME type implied by `data`'s actual byte signature, or
    None if it doesn't match any format `filetype` recognizes.

    `filetype.guess` only reads the first couple hundred bytes internally,
    so passing the full buffer is fine even for a 100MB upload - no need
    to slice it ourselves first.
    """
    if not data:
        return None
    kind = filetype.guess(data)
    return kind.mime if kind else None


def validate_media_signature(data: bytes, allowed_mimes: Set[str], category: str) -> str:
    """
    Confirm `data`'s real file signature is one of `allowed_mimes`.

    Returns the detected MIME type on success. Raises
    `UnsupportedMediaError` on a mismatch or an undetectable format -
    this is the authoritative check; any Content-Type header or filename
    extension the caller also has is informational only and must not be
    used as a substitute for this.

    `category` is a short human word ("image" / "video") used only to
    build the client-facing error message.
    """
    mime = sniff_mime(data)
    if mime is None or mime not in allowed_mimes:
        raise UnsupportedMediaError(
            f"File content does not match a supported {category} format."
        )
    return mime
