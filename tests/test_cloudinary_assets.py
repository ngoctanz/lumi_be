from io import BytesIO

from PIL import Image

from lumi_be.cloud_images import StoredImage, prepare_image


class FakeImageStore:
    def __init__(self):
        self.uploaded = []
        self.deleted = []

    def upload(self, user_id, asset_id, image):
        self.uploaded.append((user_id, asset_id, image))
        return StoredImage(
            raw_url=f"https://res.cloudinary.com/test/raw/upload/{asset_id}.rgb565",
            raw_public_id=f"lumi/users/{user_id}/assets/{asset_id}/firmware.rgb565",
            preview_url=f"https://res.cloudinary.com/test/image/upload/{asset_id}.webp",
            preview_public_id=f"lumi/users/{user_id}/assets/{asset_id}/preview",
        )

    def delete(self, asset):
        self.deleted.append(asset.id)


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


def test_upload_lists_firmware_metadata_and_deletes_owned_asset(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    claim(hardware[1], users[1])
    store = FakeImageStore()
    app.state.image_store = store
    path = f"/api/v1/me/devices/{hardware[0][0]}/assets"

    response = client.post(
        path,
        headers=users[0][1],
        data={"name": "Red wallpaper"},
        files={"file": ("wallpaper.png", png(), "image/png")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["size"] == 153600
    assert body["encoding"] == "rgb565-le"
    assert body["preview_url"].startswith("https://res.cloudinary.com/")
    assert len(store.uploaded) == 1

    device_asset = client.get(f"/api/v1/device/assets/{body['id']}", headers=hardware[0][1])
    assert device_asset.status_code == 200
    assert device_asset.json()["url"].endswith(".rgb565")
    assert "preview_url" not in device_asset.json()
    assert client.get(f"/api/v1/device/assets/{body['id']}", headers=hardware[1][1]).status_code == 404

    delete_path = f"{path}/{body['id']}"
    assert client.delete(delete_path, headers=users[1][1]).status_code == 404
    assert client.delete(delete_path, headers=users[0][1]).status_code == 204
    assert store.deleted == [body["id"]]
    assert app.state.db.assets.find_one({"_id": body["id"]}) is None


def test_upload_rejects_non_image_without_calling_cloudinary(app, client, users, hardware, claim):
    claim(hardware[0], users[0])
    store = FakeImageStore()
    app.state.image_store = store
    response = client.post(
        f"/api/v1/me/devices/{hardware[0][0]}/assets",
        headers=users[0][1],
        data={"name": "Bad file"},
        files={"file": ("bad.png", b"not an image", "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert store.uploaded == []
