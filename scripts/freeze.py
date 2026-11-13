"""In danh sách gói Python đã cài (tên==phiên bản) để quét CVE bằng pip-audit."""
import importlib.metadata as m

print("\n".join(sorted(f"{d.metadata['Name']}=={d.version}" for d in m.distributions())))
