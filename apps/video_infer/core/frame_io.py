from __future__ import annotations

import cv2


def encode_frame_jpeg(frame, width: int, height: int) -> bytes | None:
    resized = cv2.resize(frame, (width, height))
    ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return None
    return encoded.tobytes()

