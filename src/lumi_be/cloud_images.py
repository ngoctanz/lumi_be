"""Decode untrusted images and store firmware + preview variants in Cloudinary."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

import cloudinary
from PIL import Image, ImageOps, UnidentifiedImageError


class CloudImageError(Exception):
    pass


@dataclass(frozen=True)
class PreparedImage:
    raw: bytes
    preview: bytes
    sha256: str
    source_format: str


@dataclass(frozen=True)
class StoredImage:
    raw_url: str
    raw_public_id: str
    preview_url: str
    preview_public_id: str


def prepare_image(payload: bytes) -> PreparedImage:
    if not payload:
        raise ValueError("Image file is empty")
    try:
        with Image.open(BytesIO(payload)) as source:
            source_format = (source.format or "unknown").lower()
            source.load()
            if source.width > 10000 or source.height > 10000:
                raise ValueError("Image dimensions are too large")
            oriented = ImageOps.exif_transpose(source)
            image = ImageOps.fit(oriented.convert("RGB"), (320, 240), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("File is not a supported image") from exc

    rgb = image.tobytes()
    raw = bytearray(320 * 240 * 2)
    output = 0
    for offset in range(0, len(rgb), 3):
        red, green, blue = rgb[offset], rgb[offset + 1], rgb[offset + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        raw[output] = value & 0xFF
        raw[output + 1] = value >> 8
        output += 2

    preview = BytesIO()
    image.save(preview, format="WEBP", quality=82, method=4)
    raw_bytes = bytes(raw)
    return PreparedImage(raw_bytes, preview.getvalue(), sha256(raw_bytes).hexdigest(), source_format)


class CloudinaryImageStore:
    def __init__(self, settings):
        self.enabled = settings.cloudinary_enabled
        self.folder = settings.cloudinary_folder.strip("/")
        if self.enabled:
            cloudinary.config(
                cloud_name=settings.cloudinary_cloud_name,
                api_key=settings.cloudinary_api_key,
                api_secret=settings.cloudinary_api_secret.get_secret_value(),
                secure=True,
            )

    @staticmethod
    def _uploader():
        import cloudinary.uploader

        return cloudinary.uploader

    def upload(self, namespace: str, asset_id: str, image: PreparedImage) -> StoredImage:
        if not self.enabled:
            raise CloudImageError("Cloudinary is not configured")
        base = f"{self.folder}/{namespace}/assets/{asset_id}"
        preview_id = f"{base}/preview"
        raw_id = f"{base}/firmware.rgb565"
        uploader = self._uploader()
        preview_result = None
        raw_result = None
        try:
            preview_result = uploader.upload(
                BytesIO(image.preview),
                resource_type="image",
                public_id=preview_id,
                overwrite=False,
                unique_filename=False,
            )
            raw_result = uploader.upload(
                BytesIO(image.raw),
                resource_type="raw",
                public_id=raw_id,
                overwrite=False,
                unique_filename=False,
            )
            return StoredImage(
                raw_url=raw_result["secure_url"],
                raw_public_id=raw_result["public_id"],
                preview_url=preview_result["secure_url"],
                preview_public_id=preview_result["public_id"],
            )
        except Exception as exc:
            if raw_result:
                try:
                    uploader.destroy(raw_result["public_id"], resource_type="raw", invalidate=True)
                except Exception:
                    pass
            if preview_result:
                try:
                    uploader.destroy(preview_result["public_id"], resource_type="image", invalidate=True)
                except Exception:
                    pass
            raise CloudImageError("Cloudinary upload failed") from exc

    def delete(self, asset) -> None:
        if not self.enabled:
            raise CloudImageError("Cloudinary is not configured")
        uploader = self._uploader()
        try:
            raw = uploader.destroy(asset.provider_id, resource_type="raw", invalidate=True)
            preview = uploader.destroy(asset.preview_provider_id, resource_type="image", invalidate=True)
            if raw.get("result") not in {"ok", "not found"} or preview.get("result") not in {
                "ok",
                "not found",
            }:
                raise RuntimeError("Cloudinary rejected asset deletion")
        except Exception as exc:
            raise CloudImageError("Cloudinary deletion failed") from exc
