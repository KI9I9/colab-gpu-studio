from __future__ import annotations

import io

from app import main, storage
from app.common import current_user, get_store
from app.config import settings
from app.db import Store
from app.storage import asset_path, save_asset, thumbnail_cache_path
from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes(width: int = 640, height: int = 360) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (42, 128, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_unauthenticated_asset_routes_remain_no_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    store = Store(f"sqlite:///{tmp_path / 'unauth.sqlite'}")
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(main.app)
        for suffix in ("preview", "download", "thumbnail"):
            resp = client.get(f"/api/assets/missing/{suffix}")
            assert resp.status_code == 401
            assert resp.headers.get("cache-control") == "private, no-store"
    finally:
        main.app.dependency_overrides.clear()


def test_asset_preview_download_range_etag_and_disk_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    store = Store(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    user = {"id": "u1", "role": "user"}
    project = store.create("projects", user["id"], {"title": "cache-test"})
    img = save_asset(store, user["id"], project["id"], _png_bytes(), "frame.png", source_job_id="job-1")
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + (b"\x11\x22\x33\x44" * 24)
    vid = save_asset(store, user["id"], project["id"], mp4_bytes, "clip.mp4", source_job_id="job-1")

    main.app.dependency_overrides[get_store] = lambda: store
    main.app.dependency_overrides[current_user] = lambda: user
    try:
        client = TestClient(main.app)

        # 1. Preview returns inline + private immutable + strong ETag, supporting 304.
        prev = client.get(f"/api/assets/{img['id']}/preview")
        assert prev.status_code == 200
        assert prev.headers.get("cache-control") == "private, max-age=31536000, immutable"
        assert prev.headers.get("content-disposition", "").startswith("inline")
        assert prev.headers.get("etag") == f'"{img["sha256"]}"'
        assert "cookie" not in prev.headers.get("vary", "").lower()

        not_modified = client.get(
            f"/api/assets/{img['id']}/preview",
            headers={"If-None-Match": prev.headers["etag"]},
        )
        assert not_modified.status_code == 304
        assert not_modified.headers.get("cache-control") == "private, max-age=31536000, immutable"
        assert not_modified.headers.get("etag") == prev.headers["etag"]

        # 2. Range request on /preview returns 206 Partial Content while keeping immutable caching.
        ranged = client.get(
            f"/api/assets/{vid['id']}/preview",
            headers={"Range": "bytes=0-15"},
        )
        assert ranged.status_code == 206
        assert len(ranged.content) == 16
        assert ranged.content == mp4_bytes[:16]
        assert ranged.headers.get("cache-control") == "private, max-age=31536000, immutable"
        assert ranged.headers.get("content-disposition", "").startswith("inline")

        # 3. Download stays distinct from /preview and forces Content-Disposition: attachment.
        dl = client.get(f"/api/assets/{vid['id']}/download")
        assert dl.status_code == 200
        assert dl.content == mp4_bytes
        assert dl.headers.get("cache-control") == "private, max-age=31536000, immutable"
        assert dl.headers.get("content-disposition", "").startswith("attachment")

        # 4. Thumbnail is generated once, cached on disk under data_dir/thumbs, and reused on disk hit.
        thumb1 = client.get(f"/api/assets/{img['id']}/thumbnail")
        assert thumb1.status_code == 200
        assert thumb1.headers.get("content-type") == "image/webp"
        assert thumb1.headers.get("cache-control") == "private, max-age=31536000, immutable"
        cached_file = thumbnail_cache_path(img)
        assert cached_file.is_file() and cached_file.stat().st_size > 0

        def _fail_redecode(*_args, **_kwargs):
            raise AssertionError("PIL.Image.open should not run on thumbnail disk cache hit")

        monkeypatch.setattr(storage.Image, "open", _fail_redecode)
        thumb2 = client.get(f"/api/assets/{img['id']}/thumbnail")
        assert thumb2.status_code == 200
        assert thumb2.content == thumb1.content
        assert thumb2.headers.get("cache-control") == "private, max-age=31536000, immutable"

        # 5. Non-asset JSON endpoints must retain strict private, no-store headers.
        gal = client.get(f"/api/gpu/gallery?project_id={project['id']}")
        assert gal.status_code == 200
        assert gal.headers.get("cache-control") == "private, no-store"

        refs = client.get(f"/api/assets?project_id={project['id']}")
        assert refs.status_code == 200
        assert refs.headers.get("cache-control") == "private, no-store"

        # 6. Deleting an asset removes both original binary and cached thumbnail file.
        assert asset_path(img).is_file()
        deleted = client.delete(f"/api/assets/{img['id']}")
        assert deleted.status_code == 200
        assert deleted.headers.get("cache-control") == "private, no-store"
        assert not asset_path(img).exists()
        assert not cached_file.exists()
    finally:
        main.app.dependency_overrides.clear()
