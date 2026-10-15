from io import BytesIO

from PIL import Image

from lumi_be.cloud_images import prepare_image


def png(width=640, height=480, color=(255, 0, 0)):
    target = BytesIO()
    Image.new("RGB", (width, height), color).save(target, "PNG")
    return target.getvalue()


def test_conversion_is_fixed_rgb565_little_endian():
    image = prepare_image(png(width=1, height=1))
    assert image.raw[:2] == b"\x00\xf8"
    assert len(image.raw) == 153600
    assert len(image.sha256) == 64
    assert image.source_format == "png"
