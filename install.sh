#!/usr/bin/env bash
# =============================================================================
# TrucCa – script cài đặt / nâng cấp bằng Docker Compose
#
# Cách dùng nhanh (hỏi từng thông tin):
#   ./install.sh
#
# Không hỏi (dùng trong tự động hoá):
#   ./install.sh -u http://10.0.0.5:9093 -d /opt/trucca -p 8080 -y
#
# Script sẽ:
#   1. Kiểm tra docker / docker compose.
#   2. Nạp image TrucCa + PostgreSQL: dùng image đã có, hoặc `docker load` file trucca-image-<version>.tar.gz /
#      postgres-<tag>.tar.gz (cạnh script, trong dist/, hoặc -i), hoặc tự tải từ GitHub Release (gh / curl).
#   3. Tạo thư mục cài đặt: <dir>/config (config.yml), <dir>/data (PostgreSQL + báo cáo PDF), <dir>/trucca.env.
#   4. Ghi địa chỉ Alertmanager vào config.yml, sinh mật khẩu PostgreSQL ngẫu nhiên (lần đầu).
#   5. Chạy docker compose và chờ tới khi các container healthy.
#
# Chạy lại script với version mới = nâng cấp (giữ nguyên cấu hình, mật khẩu, dữ liệu).
# =============================================================================
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
APP_UID=10001

VERSION="$(grep -E '^TRUCCA_VERSION=' "$REPO_DIR/.env.example" | head -1 | cut -d= -f2)"
IMAGE_REPO="$(grep -E '^TRUCCA_IMAGE=' "$REPO_DIR/.env.example" | head -1 | cut -d= -f2)"
IMAGE_REPO="${IMAGE_REPO:-registry.local/noc/trucca}"
GITHUB_REPO="${TRUCCA_GITHUB_REPO:-haiops18/trucca}"

AM_URL=""
BASE_DIR=""
PORT=""
IMAGE_FILE=""
PROJECT="trucca"
ASSUME_YES=0

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_bld=$'\e[1m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_red=""; c_grn=""; c_ylw=""; c_bld=""; c_off=""; }
info() { printf '%s==>%s %s\n' "$c_bld" "$c_off" "$*"; }
ok()   { printf '%s ✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s ! %s%s\n' "$c_ylw" "$*" "$c_off" >&2; }
die()  { printf '%sLỖI:%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }
trap 'die "dừng ở dòng $LINENO (lệnh: $BASH_COMMAND)"' ERR

usage() {
  cat <<EOF
Cài đặt / nâng cấp TrucCa $VERSION

Tuỳ chọn (thiếu tuỳ chọn nào thì script hỏi):
  -u, --alertmanager-url URL   địa chỉ Alertmanager, vd http://10.0.0.5:9093
  -d, --dir DIR                thư mục lưu cấu hình + dữ liệu (mặc định /opt/trucca)
  -p, --port PORT              cổng web trên máy này (mặc định 8080)
  -i, --image-file FILE        file image (trucca-image-$VERSION.tar.gz)
      --project NAME           tên compose project (mặc định trucca)
  -y, --yes                    không hỏi, dùng giá trị mặc định cho mục không truyền
  -h, --help                   hướng dẫn này
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--alertmanager-url) AM_URL="${2:-}"; shift 2 ;;
    -d|--dir) BASE_DIR="${2:-}"; shift 2 ;;
    -p|--port) PORT="${2:-}"; shift 2 ;;
    -i|--image-file) IMAGE_FILE="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "tuỳ chọn không hợp lệ: $1" ;;
  esac
done

# ---------------------------------------------------------------- tiện ích
SUDO=""
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo"
fi

ask() {  # ask "Câu hỏi" "mặc định" -> REPLY
  local q="$1" def="${2:-}"
  if [[ $ASSUME_YES -eq 1 || ! -t 0 ]]; then REPLY="$def"; return; fi
  if [[ -n "$def" ]]; then read -r -p "$q [$def]: " REPLY; REPLY="${REPLY:-$def}"
  else read -r -p "$q: " REPLY; fi
}

confirm() {  # confirm "Câu hỏi" -> 0 nếu đồng ý
  [[ $ASSUME_YES -eq 1 ]] && return 0
  [[ -t 0 ]] || return 1
  local a; read -r -p "$1 [y/N]: " a; [[ "$a" =~ ^[YyCc]$ ]]
}

set_env() {  # set_env FILE KEY VALUE  (thay dòng KEY=... hoặc thêm mới)
  local f="$1" k="$2" v="$3" tmp
  tmp="$(mktemp)"
  if grep -qE "^${k}=" "$f"; then
    awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$f" > "$tmp"
  else
    cat "$f" > "$tmp"; printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  cat "$tmp" > "$f"; rm -f "$tmp"
}

get_env() { grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true; }

random_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 24
  else LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 48; fi
}

port_in_use() {
  if command -v ss >/dev/null 2>&1; then ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; fi
}

# ---------------------------------------------------------------- 1. kiểm tra môi trường
info "Kiểm tra Docker"
command -v docker >/dev/null 2>&1 || die "chưa cài Docker (https://docs.docker.com/engine/install/)"
docker info >/dev/null 2>&1 || die "không gọi được Docker daemon (chạy bằng root hoặc user thuộc nhóm docker)"
docker compose version >/dev/null 2>&1 || die "thiếu Docker Compose v2 (lệnh 'docker compose')"
[[ -f "$COMPOSE_FILE" ]] || die "không thấy $COMPOSE_FILE – hãy chạy script trong thư mục repo TrucCa"
[[ -n "$VERSION" ]] || die "không đọc được TRUCCA_VERSION trong .env.example"
ok "Docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short)"

# ---------------------------------------------------------------- 2. thu thập thông tin
echo
info "Thông tin cài đặt TrucCa $VERSION"
while true; do
  [[ -n "$AM_URL" ]] || { ask "Địa chỉ Alertmanager (vd http://10.0.0.5:9093)" ""; AM_URL="$REPLY"; }
  AM_URL="${AM_URL%/}"
  if [[ "$AM_URL" =~ ^https?://[^[:space:]/]+(:[0-9]+)?(/[^[:space:]]*)?$ ]]; then break; fi
  [[ $ASSUME_YES -eq 1 || ! -t 0 ]] && die "địa chỉ Alertmanager không hợp lệ: '$AM_URL' (cần dạng http://host:9093)"
  warn "Địa chỉ không hợp lệ, cần dạng http://host:9093 hoặc https://..."; AM_URL=""
done
if [[ "$AM_URL" =~ ^https?://(localhost|127\.[0-9.]+)([:/]|$) ]]; then
  warn "'localhost' bên trong container là chính container TrucCa, không phải máy chủ."
  warn "Hãy dùng IP/hostname thật của máy chạy Alertmanager."
  confirm "Vẫn dùng địa chỉ này?" || die "đã huỷ"
fi

[[ -n "$BASE_DIR" ]] || { ask "Thư mục lưu cấu hình và dữ liệu" "/opt/trucca"; BASE_DIR="$REPLY"; }
[[ "$BASE_DIR" == /* ]] || BASE_DIR="$(pwd)/$BASE_DIR"
BASE_DIR="${BASE_DIR%/}"
[[ -n "$BASE_DIR" && "$BASE_DIR" != "/" ]] || die "thư mục cài đặt không hợp lệ"

CONFIG_DIR="$BASE_DIR/config"
DATA_DIR="$BASE_DIR/data"
ENV_FILE="$BASE_DIR/trucca.env"

DEFAULT_PORT="$(get_env "$ENV_FILE" TRUCCA_PORT)"
[[ -n "$PORT" ]] || { ask "Cổng web trên máy này" "${DEFAULT_PORT:-8080}"; PORT="$REPLY"; }
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "cổng không hợp lệ: $PORT"

RUNNING_PORT="$(docker compose -p "$PROJECT" ps -q trucca 2>/dev/null | xargs -r docker port 2>/dev/null | grep -oE ':[0-9]+$' | head -1 | tr -d : || true)"
if port_in_use "$PORT" && [[ "$RUNNING_PORT" != "$PORT" ]]; then
  die "cổng $PORT đang được chương trình khác dùng – chọn cổng khác bằng -p"
fi

echo
echo "  Alertmanager : $AM_URL"
echo "  Thư mục      : $BASE_DIR  (config/, data/, trucca.env)"
echo "  Cổng web     : $PORT"
echo "  Image        : $IMAGE_REPO:$VERSION"
echo "  Project      : $PROJECT"
echo
confirm "Tiếp tục cài đặt?" || die "đã huỷ"

# ---------------------------------------------------------------- 3. nạp image
# Mỗi image tìm theo thứ tự: đã có trên máy -> file cạnh install.sh / dist/ / thư mục hiện tại
# -> tải từ GitHub Release v<version> (gh, hoặc curl vì repo public) -> docker pull (chỉ PostgreSQL, cần internet).
DIST_DIR="$REPO_DIR/dist"
RELEASE_URL="https://github.com/$GITHUB_REPO/releases/download/v$VERSION"

load_image_file() {
  local f="$1"
  if [[ -f "$f.sha256" ]] && command -v sha256sum >/dev/null 2>&1; then
    (cd "$(dirname "$f")" && sha256sum -c --status "$(basename "$f").sha256") \
      || die "file image bị hỏng (sai SHA-256): $f – xoá file và tải lại"
    ok "SHA-256 khớp: $(basename "$f")"
  fi
  docker load -i "$f" | sed 's/^/    /'
}

download_asset() {  # download_asset TÊN_FILE -> 0 nếu tải được vào $DIST_DIR
  local name="$1"
  mkdir -p "$DIST_DIR"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh release download "v$VERSION" -R "$GITHUB_REPO" -D "$DIST_DIR" --clobber -p "$name" -p "$name.sha256" \
      >/dev/null 2>&1 && return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --progress-bar -o "$DIST_DIR/$name.part" "$RELEASE_URL/$name" \
      && mv "$DIST_DIR/$name.part" "$DIST_DIR/$name" \
      && curl -fsSL --retry 3 -o "$DIST_DIR/$name.sha256" "$RELEASE_URL/$name.sha256" \
      && return 0
    rm -f "$DIST_DIR/$name.part"
  fi
  return 1
}

ensure_image() {  # ensure_image IMAGE:TAG TÊN_FILE CHO_PHÉP_PULL(0/1)
  local ref="$1" name="$2" allow_pull="$3" f found=""
  if docker image inspect "$ref" >/dev/null 2>&1; then ok "$ref đã có sẵn trên máy"; return; fi
  for f in "$REPO_DIR/$name" "$DIST_DIR/$name" "$(pwd)/$name"; do
    [[ -f "$f" ]] && { found="$f"; break; }
  done
  if [[ -z "$found" ]]; then
    info "Tải $name từ GitHub Release v$VERSION"
    download_asset "$name" && found="$DIST_DIR/$name"
  fi
  if [[ -n "$found" ]]; then
    load_image_file "$found"
  elif [[ "$allow_pull" == 1 ]]; then
    info "docker pull $ref"
    docker pull "$ref" || true
  fi
  docker image inspect "$ref" >/dev/null 2>&1 || die "không nạp được image $ref.
       Tải $name (và $name.sha256) tại https://github.com/$GITHUB_REPO/releases/tag/v$VERSION,
       đặt cạnh install.sh rồi chạy lại."
  ok "$ref sẵn sàng"
}

PG_IMAGE="$(awk '/^  postgres:/{f=1} f && /^    image:/{print $2; exit}' "$COMPOSE_FILE")"
[[ -n "$PG_IMAGE" ]] || die "không đọc được image PostgreSQL trong docker-compose.yml"
PG_FILE="$(printf '%s' "$PG_IMAGE" | tr ':/' '--').tar.gz"     # postgres:16.4-alpine -> postgres-16.4-alpine.tar.gz

info "Nạp image"
if [[ -n "$IMAGE_FILE" ]]; then
  [[ -f "$IMAGE_FILE" ]] || die "không thấy file image: $IMAGE_FILE"
  load_image_file "$IMAGE_FILE"
fi
ensure_image "$IMAGE_REPO:$VERSION" "trucca-image-$VERSION.tar.gz" 0
ensure_image "$PG_IMAGE" "$PG_FILE" 1

# ---------------------------------------------------------------- 4. thư mục + cấu hình
info "Chuẩn bị thư mục $BASE_DIR"
$SUDO mkdir -p "$CONFIG_DIR" "$DATA_DIR/trucca" "$DATA_DIR/postgres"
$SUDO chown -R "$APP_UID:$APP_UID" "$DATA_DIR/trucca"   # container TrucCa chạy non-root UID 10001
ok "config/, data/trucca (UID $APP_UID), data/postgres"

CFG="$CONFIG_DIR/config.yml"
if [[ ! -f "$CFG" ]]; then
  $SUDO cp "$REPO_DIR/config/config.example.yml" "$CFG"
  ok "tạo $CFG từ config.example.yml"
else
  $SUDO cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)"
  ok "giữ nguyên $CFG (đã sao lưu bản cũ)"
fi
# Ghi alertmanager.url (dòng url: đầu tiên nằm trong khối alertmanager:)
tmp="$(mktemp)"
awk -v url="$AM_URL" '
  /^[^[:space:]#]/ { inblk = ($0 ~ /^alertmanager:/) }
  inblk && !done && /^[[:space:]]+url:/ { match($0, /^[[:space:]]+/); print substr($0, 1, RLENGTH) "url: \"" url "\""; done=1; next }
  { print }
  END { if (!done) exit 3 }' "$CFG" > "$tmp" || { rm -f "$tmp"; die "không tìm thấy alertmanager.url trong $CFG"; }
$SUDO cp "$tmp" "$CFG"; rm -f "$tmp"
$SUDO chmod 644 "$CFG"
ok "alertmanager.url = $AM_URL"

# ---------------------------------------------------------------- 5. file biến môi trường (secret)
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -n "$(ls -A "$DATA_DIR/postgres" 2>/dev/null)" ]]; then
    die "$DATA_DIR/postgres đã có dữ liệu nhưng thiếu $ENV_FILE (chứa mật khẩu PostgreSQL).
       Khôi phục file đó, hoặc chọn thư mục cài đặt khác."
  fi
  $SUDO install -m 600 "$REPO_DIR/.env.example" "$ENV_FILE"
  $SUDO chown "$(id -u):$(id -g)" "$ENV_FILE"
  set_env "$ENV_FILE" POSTGRES_PASSWORD "$(random_secret)"
  set_env "$ENV_FILE" TRUCCA_ADMIN_USERNAME ""
  set_env "$ENV_FILE" TRUCCA_ADMIN_PASSWORD ""
  ok "tạo $ENV_FILE (mật khẩu PostgreSQL sinh ngẫu nhiên, quyền 600)"
else
  ok "giữ nguyên mật khẩu trong $ENV_FILE"
fi
set_env "$ENV_FILE" TRUCCA_IMAGE "$IMAGE_REPO"
set_env "$ENV_FILE" TRUCCA_VERSION "$VERSION"
set_env "$ENV_FILE" TRUCCA_PORT "$PORT"
set_env "$ENV_FILE" TRUCCA_CONFIG_DIR "$CONFIG_DIR"
set_env "$ENV_FILE" TRUCCA_DATA_DIR "$DATA_DIR"
set_env "$ENV_FILE" TRUCCA_ENV_FILE "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------- 6. kiểm tra Alertmanager (chỉ cảnh báo)
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -m 5 -o /dev/null "$AM_URL/-/healthy" 2>/dev/null || curl -fsS -m 5 -o /dev/null "$AM_URL/api/v2/status" 2>/dev/null; then
    ok "kết nối được Alertmanager từ máy này"
  else
    warn "chưa kết nối được $AM_URL từ máy này (Alertmanager có thể cần xác thực, hoặc khác mạng)."
    warn "TrucCa vẫn được cài; trạng thái kết nối hiển thị trên giao diện. Xác thực: xem ALERTMANAGER_* trong $ENV_FILE."
  fi
fi

# ---------------------------------------------------------------- 7. chạy docker compose
compose() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
info "Khởi động TrucCa (docker compose)"
compose config -q || die "docker-compose.yml / $ENV_FILE không hợp lệ"
if ! compose up -d --no-build --wait --wait-timeout 240 postgres trucca; then
  compose logs --tail 40 trucca >&2 || true
  die "container chưa healthy – xem log ở trên (docker compose -p $PROJECT logs trucca)"
fi

HEALTH="$(curl -fsS -m 5 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
ok "healthz: ${HEALTH:-không đọc được}"

# IP của card mạng ra ngoài (tránh IP bridge của Docker)
HOST_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}' || true)"
[[ -n "$HOST_IP" ]] || HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
cat <<EOF

${c_grn}${c_bld}TrucCa $VERSION đã chạy.${c_off}

  Mở trình duyệt : http://${HOST_IP:-<ip-máy-chủ>}:$PORT
  Đăng nhập lần đầu: admin / admin  (hệ thống bắt buộc đổi mật khẩu ngay)

  Cấu hình        : $CFG   (sửa xong tự nạp lại trong ~10 giây)
  Biến môi trường : $ENV_FILE  (mật khẩu PostgreSQL, xác thực Alertmanager)
  Dữ liệu         : $DATA_DIR

  Xem log   : docker compose -p $PROJECT --env-file $ENV_FILE -f $COMPOSE_FILE logs -f trucca
  Dừng      : docker compose -p $PROJECT --env-file $ENV_FILE -f $COMPOSE_FILE down
  Nâng cấp  : git pull, tải image version mới rồi chạy lại ./install.sh -d $BASE_DIR
EOF
