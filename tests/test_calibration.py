from io import BytesIO

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from phone_gateway.app import create_app
from phone_gateway.calibration import calibrate, create_board, detect_photo

ORIGIN = {"origin": "http://testserver"}


def synthetic_photos():
    """Quatro vistas inclinadas do mesmo tabuleiro para testar o pipeline real de CV."""
    board = create_board().generateImage((1280, 960))
    source = np.float32([[0, 0], [1279, 0], [1279, 959], [0, 959]])
    destinations = [
        [[100, 90], [1350, 140], [1310, 1060], [120, 1040]],
        [[210, 60], [1390, 220], [1240, 1120], [100, 900]],
        [[55, 260], [1250, 95], [1450, 1020], [220, 1130]],
        [[160, 170], [1400, 70], [1320, 1010], [230, 1080]],
    ]
    photos = []
    for corners in destinations:
        transform = cv2.getPerspectiveTransform(source, np.float32(corners))
        view = cv2.warpPerspective(board, transform, (1600, 1200), borderValue=255)
        ok, jpeg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 96])
        assert ok
        photos.append(jpeg.tobytes())
    return photos


def test_legacy_board_detection_and_npz_fields():
    photos = synthetic_photos()
    assert detect_photo(photos[0])[3] >= 8
    result = calibrate(photos)
    buffer = BytesIO()
    np.savez_compressed(buffer, **result)
    with np.load(BytesIO(buffer.getvalue()), allow_pickle=False) as saved:
        assert saved["K"].shape == (3, 3)
        assert saved["dist"].shape[0] == 1
        assert saved["rt"].shape == (3, 4)
        assert saved["nK"].shape == (3, 3)
        assert saved["roi"].shape == (4,)
        assert (int(saved["w"]), int(saved["h"])) == (1600, 1200)
        assert float(saved["rms"]) > 0
        assert bool(saved["charuco_legacy"])
    assert "rt" not in calibrate(photos[:3], include_rt=False)


def test_calibration_api_saves_downloads_and_requires_explicit_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-token")
    photos = synthetic_photos()
    with TestClient(create_app(calibration_dir=tmp_path)) as client:
        headers = {**ORIGIN, "x-gateway-token": "test-token"}
        image = client.get("/api/calibration/board")
        assert image.status_code == 200
        assert cv2.imdecode(np.frombuffer(image.content, np.uint8), cv2.IMREAD_GRAYSCALE).shape == (
            2280,
            3040,
        )
        inspected = client.post(
            "/api/calibration/inspect",
            headers=headers,
            files={"photo": ("photo.jpg", photos[0], "image/jpeg")},
        )
        assert inspected.status_code == 200
        assert inspected.json()["corners"] >= 8
        files = [
            ("photos", (f"{index}.jpg", photo, "image/jpeg"))
            for index, photo in enumerate(photos[:3])
        ]
        response = client.post(
            "/api/calibration",
            headers=headers,
            data={"device_id": "my-phone", "include_rt": "true"},
            files=files,
        )
        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "my-phone_1600x1200.npz"
        assert response.json()["download_url"] == "/api/calibration/my-phone/1600x1200/download"
        assert (tmp_path / "my-phone_1600x1200.npz").is_file()
        downloaded = client.get(response.json()["download_url"], headers=headers)
        assert downloaded.status_code == 200
        with np.load(BytesIO(downloaded.content), allow_pickle=False) as saved:
            assert saved["K"].shape == (3, 3)
        assert (
            client.post(
                "/api/calibration", headers=headers, data={"device_id": "my-phone"}, files=files
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/calibration",
                headers=headers,
                data={"device_id": "my-phone", "replace": "true", "include_rt": "false"},
                files=files,
            ).status_code
            == 200
        )
        with np.load(tmp_path / "my-phone_1600x1200.npz", allow_pickle=False) as saved:
            assert "rt" not in saved
        other_size = []
        for index, photo in enumerate(photos[:3]):
            frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
            resized = cv2.resize(frame, (1280, 960))
            other_size.append(
                (
                    "photos",
                    (
                        f"smaller-{index}.jpg",
                        cv2.imencode(".jpg", resized)[1].tobytes(),
                        "image/jpeg",
                    ),
                )
            )
        another = client.post(
            "/api/calibration", headers=headers, data={"device_id": "my-phone"}, files=other_size
        )
        assert another.status_code == 200, another.text
        assert another.json()["filename"] == "my-phone_1280x960.npz"
        assert (tmp_path / "my-phone_1600x1200.npz").is_file()
        assert (tmp_path / "my-phone_1280x960.npz").is_file()
        assert (
            client.get("/api/calibration/my-phone/bad/download", headers=headers).status_code == 422
        )


def test_calibration_rejects_invalid_board_mixed_sizes_and_access(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-token")
    photos = synthetic_photos()
    with TestClient(create_app(calibration_dir=tmp_path)) as client:
        valid = {**ORIGIN, "x-gateway-token": "test-token"}
        white = cv2.imencode(".jpg", np.full((1200, 1600), 255, np.uint8))[1].tobytes()
        assert (
            client.post(
                "/api/calibration/inspect",
                headers=valid,
                files={"photo": ("white.jpg", white, "image/jpeg")},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/calibration/inspect",
                headers={"origin": "https://elsewhere.test"},
                files={"photo": ("photo.jpg", photos[0], "image/jpeg")},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/calibration/inspect",
                headers=ORIGIN,
                files={"photo": ("photo.jpg", photos[0], "image/jpeg")},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/calibration",
                headers=valid,
                data={"device_id": "../escape"},
                files=[
                    ("photos", (f"{i}.jpg", photo, "image/jpeg"))
                    for i, photo in enumerate(photos[:3])
                ],
            ).status_code
            == 422
        )
        with pytest.raises(ValueError, match="mesma resolução"):
            resized = cv2.resize(cv2.imdecode(np.frombuffer(photos[2], np.uint8), 0), (800, 600))
            smaller = cv2.imencode(".jpg", resized)[1].tobytes()
            calibrate([photos[0], photos[1], smaller])
