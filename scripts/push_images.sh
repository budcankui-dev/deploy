#!/usr/bin/env bash
set -euo pipefail

# 用法:
# REGISTRY=registry.example.com/project TAG=v1 ./scripts/push_images.sh

REGISTRY="${REGISTRY:-registry.local/video-infer}"
TAG="${TAG:-latest}"

echo "[push] ${REGISTRY}/video-infer-sender:${TAG}"
docker push "${REGISTRY}/video-infer-sender:${TAG}"

echo "[push] ${REGISTRY}/video-infer-receiver:${TAG}"
docker push "${REGISTRY}/video-infer-receiver:${TAG}"

echo "[push] ${REGISTRY}/model-trainer:${TAG}"
docker push "${REGISTRY}/model-trainer:${TAG}"

echo "push done."
