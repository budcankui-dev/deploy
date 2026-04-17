#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 一次性准备脚本：把私有镜像仓库加入 docker 的 insecure-registries 白名单。
# 需要 sudo。
#
# 用法：
#   sudo REGISTRY=10.112.204.7:5000 ./deploy/setup-docker-daemon.sh
# -----------------------------------------------------------------------------

set -euo pipefail

REGISTRY="${REGISTRY:-10.112.204.7:5000}"
DAEMON_JSON="/etc/docker/daemon.json"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "需要 root，请用 sudo 运行" >&2
  exit 1
fi

mkdir -p /etc/docker

if [[ ! -s "${DAEMON_JSON}" ]]; then
  cat >"${DAEMON_JSON}" <<JSON
{
  "insecure-registries": ["${REGISTRY}"]
}
JSON
else
  python3 - "$DAEMON_JSON" "$REGISTRY" <<'PY'
import json, sys
path, registry = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    data = {}
regs = set(data.get("insecure-registries", []))
regs.add(registry)
data["insecure-registries"] = sorted(regs)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
PY
fi

echo "已写入 ${DAEMON_JSON}:"
cat "${DAEMON_JSON}"

systemctl restart docker
echo "docker 已重启，正在验证 ..."

curl -sf "http://${REGISTRY}/v2/_catalog" >/dev/null \
  && echo "[ok] 私有仓库 ${REGISTRY} 可访问" \
  || { echo "[warn] 私有仓库 ${REGISTRY} 访问失败，请检查网络/防火墙" >&2; exit 1; }
