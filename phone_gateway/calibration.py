"""Calibração intrínseca de uma câmera com o tabuleiro ChArUco legacy do LabSEA."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image as PillowImage
from PIL import UnidentifiedImageError

SQUARES_X = 8
SQUARES_Y = 6
SQUARE_LENGTH_M = 0.095
MARKER_LENGTH_M = 0.071
DICTIONARY_ID = cv2.aruco.DICT_4X4_100
MIN_PHOTOS = 3
MAX_PHOTOS = 5
MIN_CORNERS = 8
MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_PHOTO_PIXELS = 12_000_000


def create_board():
    dictionary = cv2.aruco.getPredefinedDictionary(DICTIONARY_ID)
    board = cv2.aruco.CharucoBoard(
        (SQUARES_X, SQUARES_Y), SQUARE_LENGTH_M, MARKER_LENGTH_M, dictionary
    )
    board.setLegacyPattern(True)
    return board


def board_png():
    # 4 pixels por milímetro: 3040 × 2280 px para o tabuleiro 760 × 570 mm.
    board = create_board()
    image = board.generateImage((3040, 2280))
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("Não foi possível gerar o tabuleiro ChArUco.")
    return encoded.tobytes()


def detect_photo(jpeg: bytes):
    if not jpeg or len(jpeg) > MAX_PHOTO_BYTES:
        raise ValueError("A foto deve ter no máximo 6 MiB.")
    try:
        with PillowImage.open(BytesIO(jpeg)) as image:
            if image.format != "JPEG" or image.width * image.height > MAX_PHOTO_PIXELS:
                raise ValueError("Use JPEG com até 12 megapixels.")
            image.verify()
    except (UnidentifiedImageError, OSError, PillowImage.DecompressionBombError) as error:
        raise ValueError("A foto deve ser um JPEG válido.") from error
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if frame is None:
        raise ValueError("Não foi possível decodificar a foto.")
    board = create_board()
    corners, ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(frame)
    count = 0 if ids is None else len(ids)
    if count < MIN_CORNERS:
        raise ValueError(
            f"Foram encontrados {count} cantos ChArUco. Mostre ao menos {MIN_CORNERS} "
            "cantos do tabuleiro legacy 8×6 e tente novamente."
        )
    height, width = frame.shape
    return corners, ids, (width, height), count


def calibrate(photos: list[bytes], *, include_rt=True):
    if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
        raise ValueError("Envie de 3 a 5 fotos válidas.")
    observations = [detect_photo(photo) for photo in photos]
    image_size = observations[0][2]
    if any(item[2] != image_size for item in observations):
        raise ValueError("Todas as fotos devem ter a mesma resolução e usar a mesma câmera.")
    board = create_board()
    try:
        rms, K, dist, rvecs, tvecs, _, _, per_view_errors = (
            cv2.aruco.calibrateCameraCharucoExtended(
                [item[0] for item in observations],
                [item[1] for item in observations],
                board,
                image_size,
                None,
                None,
            )
        )
    except cv2.error as error:
        raise ValueError(
            "Não foi possível calibrar com estas fotos. Use ângulos e distâncias diferentes."
        ) from error
    if not np.isfinite(rms) or not np.isfinite(K).all() or not np.isfinite(dist).all():
        raise ValueError("O resultado da calibração é inválido; tire fotos mais variadas.")
    if K[0, 0] <= 0 or K[1, 1] <= 0:
        raise ValueError("A matriz intrínseca calculada é inválida.")
    width, height = image_size
    nK, roi = cv2.getOptimalNewCameraMatrix(K, dist, image_size, 1, image_size)
    result = {
        "K": np.asarray(K, dtype=np.float64),
        "dist": np.asarray(dist, dtype=np.float64).reshape(1, -1),
        "nK": np.asarray(nK, dtype=np.float64),
        "roi": np.asarray(roi, dtype=np.int64),
        "w": np.int64(width),
        "h": np.int64(height),
        "rms": np.float64(rms),
        "views": np.int64(len(observations)),
        "per_view_errors": np.asarray(per_view_errors, dtype=np.float64).reshape(-1),
        "charuco_squares_x": np.int64(SQUARES_X),
        "charuco_squares_y": np.int64(SQUARES_Y),
        "charuco_square_length_m": np.float64(SQUARE_LENGTH_M),
        "charuco_marker_length_m": np.float64(MARKER_LENGTH_M),
        "charuco_dictionary": np.int64(DICTIONARY_ID),
        "charuco_legacy": np.bool_(True),
    }
    if include_rt:
        # Pose do tabuleiro na primeira foto válida: útil apenas se o mesmo tabuleiro
        # permanecer fixo no referencial compartilhado pelas outras câmeras.
        R, _ = cv2.Rodrigues(rvecs[0])
        result["rt"] = np.column_stack((R, np.asarray(tvecs[0]).reshape(3)))
    return result
