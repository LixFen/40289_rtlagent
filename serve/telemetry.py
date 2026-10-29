"""Sample AMD Linux sysfs device memory (GiB); no fabricated GPU statistics."""
import json
import os
from pathlib import Path
import time


def sample():
    devices = []
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        if (device / "mem_info_vram_used").exists() and (device / "vendor").read_text().strip() == "0x1002":
            devices.append(device)
    # The sysfs card number must identify the card assigned to the container.
    selected = Path("/sys/class/drm") / os.environ.get("GPU_DRM_CARD", "card0") / "device"
    if selected not in devices:
        raise RuntimeError("Assigned AMD DRM device not found")
    return dict(ts=time.time(), device_count=int(os.environ.get("GPU_ASSIGNED_COUNT", str(len(devices)))),
                drm_card=selected.parent.name, vram_gb=int((selected / "mem_info_vram_used").read_text()) / 2**30)


if __name__ == "__main__":
    target = Path(os.environ["GPU_TELEMETRY_FILE"])
    target.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            value = sample()
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(target)
        except (OSError, RuntimeError, ValueError):
            # Let the last sample become stale so readiness fails.
            pass
        time.sleep(2)
