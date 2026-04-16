from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class DetectorResult:
    image: np.ndarray
    box_count: int
    backend: str


class BaseDetector:
    backend_name = "base"

    def detect_and_draw(self, image: np.ndarray) -> DetectorResult:
        raise NotImplementedError


class BoxDetector(BaseDetector):
    backend_name = "box"

    def detect_and_draw(self, image: np.ndarray) -> DetectorResult:
        h, w = image.shape[:2]
        cv2.rectangle(image, (int(w * 0.2), int(h * 0.2)), (int(w * 0.8), int(h * 0.8)), (0, 255, 0), 2)
        return DetectorResult(image=image, box_count=1, backend=self.backend_name)


class RandomBoxDetector(BaseDetector):
    backend_name = "random_box"

    def __init__(self):
        self.rng = np.random.default_rng()

    def detect_and_draw(self, image: np.ndarray) -> DetectorResult:
        h, w = image.shape[:2]
        bw = int(self.rng.integers(max(w // 6, 10), max(w // 2, 20)))
        bh = int(self.rng.integers(max(h // 6, 10), max(h // 2, 20)))
        x1 = int(self.rng.integers(0, max(w - bw, 1)))
        y1 = int(self.rng.integers(0, max(h - bh, 1)))
        x2 = min(x1 + bw, w - 1)
        y2 = min(y1 + bh, h - 1)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(image, "random-fallback", (x1, max(y1 - 8, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
        return DetectorResult(image=image, box_count=1, backend=self.backend_name)


class YoloDetector(BaseDetector):
    backend_name = "yolo"

    def __init__(self, model_path: str, conf: float = 0.25):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise RuntimeError("ultralytics 未安装，无法启用 yolo 推理") from exc
        self.model = YOLO(model_path)
        self.conf = conf
        self.random_fallback = RandomBoxDetector()

    def detect_and_draw(self, image: np.ndarray) -> DetectorResult:
        try:
            results = self.model.predict(image, conf=self.conf, verbose=False)
            drawn = image
            box_count = 0
            if results:
                result = results[0]
                plotted = result.plot()
                if plotted is not None:
                    drawn = plotted
                if result.boxes is not None:
                    box_count = len(result.boxes)
            return DetectorResult(image=drawn, box_count=box_count, backend=self.backend_name)
        except Exception:
            # Per-frame fallback: if YOLO inference fails, draw a random box.
            return self.random_fallback.detect_and_draw(image)


def build_detector(backend: str, yolo_model: str, yolo_conf: float) -> BaseDetector:
    if backend == "yolo":
        return YoloDetector(yolo_model, yolo_conf)
    return BoxDetector()
