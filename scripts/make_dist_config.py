"""배포 폴더 마무리: 깨끗한 config.json, 사용 안내, 빈 scan_inbox.

- 빌드한 PC에서 앱으로 추가한 감시 폴더 경로는 빼고 기본 폴더(scan_inbox)만 남김
- docs/portable_guide.txt를 '사용안내.txt'로 복사 (메모장에서 바로 열리도록 UTF-8 BOM)

사용: python scripts/make_dist_config.py <원본 config.json> <배포 폴더>
"""

import json
import os
import sys

GUIDE_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "portable_guide.txt")


def main(src_config: str, dist_dir: str) -> None:
    with open(src_config, "r", encoding="utf-8") as f:
        config = json.load(f)
    # 개발 PC의 '바탕화면\새 폴더' 같은 경로가 사내 PC로 따라가지 않게 함
    config["watch_folders"] = ["./scan_inbox"]
    with open(os.path.join(dist_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with open(GUIDE_SRC, "r", encoding="utf-8") as f:
        guide = f.read()
    with open(os.path.join(dist_dir, "사용안내.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(guide)

    os.makedirs(os.path.join(dist_dir, "scan_inbox"), exist_ok=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
