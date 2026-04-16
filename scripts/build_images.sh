#!/usr/bin/env bash
set -euo pipefail

# 用法:
# REGISTRY=registry.example.com/project TAG=v1 ./scripts/build_images.sh
# 仅推理两节点、跳过 trainer: SKIP_MODEL_TRAINER=true ./scripts/build_images.sh
# 构建阶段使用宿主机网络（pip/npm 走宿主 DNS/代理）: DOCKER_BUILD_NETWORK=host ./scripts/build_images.sh
# BuildKit 缓存（pip/npm 重复构建显著加速）: 已默认开启

export DOCKER_BUILDKIT=1

REGISTRY="${REGISTRY:-registry.local/video-infer}"
TAG="${TAG:-latest}"
SKIP_MODEL_TRAINER="${SKIP_MODEL_TRAINER:-false}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-}"

NET_ARGS=()
if [[ -n "${DOCKER_BUILD_NETWORK}" ]]; then
  NET_ARGS=(--network "${DOCKER_BUILD_NETWORK}")
  echo "[build] docker build 使用 --network ${DOCKER_BUILD_NETWORK}"
fi

INSTALL_YOLO="${INSTALL_YOLO:-false}"
INSTALL_YOLO_RECEIVER="${INSTALL_YOLO_RECEIVER:-}"
INSTALL_YOLO_SENDER="${INSTALL_YOLO_SENDER:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"
NPM_REGISTRY="${NPM_REGISTRY:-}"
# receiver 镜像：默认使用 gocv/opencv Ubuntu（完整 OpenCV/图形栈），再 pip 安装 PyTorch cu124 wheel
OPENCV_BASE_IMAGE="${OPENCV_BASE_IMAGE:-}"

if [[ -z "${INSTALL_YOLO_RECEIVER}" ]]; then
  INSTALL_YOLO_RECEIVER="${INSTALL_YOLO}"
fi
# sender 只发 JPEG，默认不装 ultralytics/torch；需要时显式 INSTALL_YOLO_SENDER=true
if [[ -z "${INSTALL_YOLO_SENDER}" ]]; then
  INSTALL_YOLO_SENDER="false"
fi

echo "[build] INSTALL_YOLO_RECEIVER=${INSTALL_YOLO_RECEIVER} INSTALL_YOLO_SENDER=${INSTALL_YOLO_SENDER}"

COMMON_BUILD_ARGS=()
if [[ -n "${PIP_INDEX_URL}" ]]; then
  COMMON_BUILD_ARGS+=(--build-arg PIP_INDEX_URL="${PIP_INDEX_URL}")
fi
if [[ -n "${PIP_TRUSTED_HOST}" ]]; then
  COMMON_BUILD_ARGS+=(--build-arg PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}")
fi
if [[ -n "${NPM_REGISTRY}" ]]; then
  COMMON_BUILD_ARGS+=(--build-arg NPM_REGISTRY="${NPM_REGISTRY}")
fi

echo "[build] video-infer-sender image..."
docker build "${NET_ARGS[@]}" -f deploy/docker/Dockerfile.video-infer-sender \
  "${COMMON_BUILD_ARGS[@]}" \
  --build-arg INSTALL_YOLO="${INSTALL_YOLO_SENDER}" \
  -t "${REGISTRY}/video-infer-sender:${TAG}" .

echo "[build] video-infer-receiver image..."
RECEIVER_BUILD_ARGS=("${COMMON_BUILD_ARGS[@]}")
if [[ -n "${OPENCV_BASE_IMAGE}" ]]; then
  RECEIVER_BUILD_ARGS+=(--build-arg OPENCV_BASE_IMAGE="${OPENCV_BASE_IMAGE}")
fi
docker build "${NET_ARGS[@]}" -f deploy/docker/Dockerfile.video-infer-receiver \
  "${RECEIVER_BUILD_ARGS[@]}" \
  --build-arg INSTALL_YOLO="${INSTALL_YOLO_RECEIVER}" \
  -t "${REGISTRY}/video-infer-receiver:${TAG}" .

if [[ "${SKIP_MODEL_TRAINER}" == "true" ]]; then
  echo "[build] SKIP_MODEL_TRAINER=true，跳过 model-trainer"
else
  echo "[build] model-trainer image..."
  docker build "${NET_ARGS[@]}" -f deploy/docker/Dockerfile.model-trainer \
    "${COMMON_BUILD_ARGS[@]}" \
    -t "${REGISTRY}/model-trainer:${TAG}" .
fi

echo "done:"
echo "  ${REGISTRY}/video-infer-sender:${TAG}"
echo "  ${REGISTRY}/video-infer-receiver:${TAG}"
if [[ "${SKIP_MODEL_TRAINER}" != "true" ]]; then
  echo "  ${REGISTRY}/model-trainer:${TAG}"
fi
