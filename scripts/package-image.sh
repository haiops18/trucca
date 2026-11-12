#!/usr/bin/env bash
# Đóng gói image TrucCa thành file để phát hành (GitHub Release) và cài bằng install.sh.
#   ./scripts/package-image.sh            # dùng TRUCCA_VERSION trong .env.example
#   ./scripts/package-image.sh 2.0.0
# Kết quả: dist/trucca-image-<version>.tar.gz và .sha256
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(grep -E '^TRUCCA_VERSION=' "$REPO_DIR/.env.example" | cut -d= -f2)}"
IMAGE="$(grep -E '^TRUCCA_IMAGE=' "$REPO_DIR/.env.example" | cut -d= -f2):$VERSION"
OUT="$REPO_DIR/dist/trucca-image-$VERSION.tar.gz"

docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "Chưa có image $IMAGE – chạy 'docker compose build' trước" >&2; exit 1; }
mkdir -p "$REPO_DIR/dist"
echo "Đóng gói $IMAGE -> $OUT"
docker save "$IMAGE" | gzip -6 > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
(cd "$REPO_DIR/dist" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
ls -lh "$OUT"
cat "$OUT.sha256"
