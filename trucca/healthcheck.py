"""Healthcheck cho Docker: gọi /healthz trên chính container (đọc port/base_path từ config)."""

import sys
import urllib.request

from trucca.config import load_config


def main() -> int:
    try:
        cfg = load_config()
        base = "" if cfg.server.base_path == "/" else cfg.server.base_path
        url = f"http://127.0.0.1:{cfg.server.port}{base}/healthz"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - URL nội bộ cố định
            return 0 if resp.status == 200 else 1
    except Exception as e:
        print(f"healthcheck failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
