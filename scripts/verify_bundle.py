"""소스 실행 꾸러미 점검: 빠지거나 바뀐 파일이 없는지, 동봉 파이썬으로 앱을 불러올 수 있는지 확인.

내부망으로 옮기는 중에 보안 프로그램이 파일을 지우는 일이 있어, 실행이 안 될 때
원인을 바로 알 수 있게 한다. 꾸러미 폴더의 check.bat 이 이 스크립트를 실행한다.

사용: runtime\\python.exe scripts\\verify_bundle.py
종료 코드: 0 = 정상, 1 = 문제 있음
"""

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "bundle_manifest.json")
# 없어도 앱 실행에는 지장이 없는 파일 (안내문 등)
OPTIONAL = {"사용안내.txt", "README.md", "CHANGELOG.md", "check.bat", "실행하기.bat"}
SHOW_MAX = 30


def check_files() -> list:
    problems = []
    if not os.path.isfile(MANIFEST):
        return ["bundle_manifest.json 이 없습니다 (파일 목록을 확인할 수 없음)"]
    with open(MANIFEST, "r", encoding="utf-8") as f:
        info = json.load(f)
    for rel, (size, digest) in sorted(info["files"].items()):
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        optional = rel in OPTIONAL or rel == "config.json"  # config.json은 앱이 고쳐 씀
        if not os.path.isfile(path):
            if not optional:
                problems.append(f"없음: {rel}")
            continue
        if optional:
            continue
        with open(path, "rb") as f:
            data = f.read()
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"바뀜: {rel}")
    return problems


def check_imports() -> list:
    problems = []
    sys.path.insert(0, ROOT)
    for name in ("tkinter", "sqlite3", "ctypes", "winreg", "zlib", "hashlib",
                 "core.database", "parsers.rule_parser", "collectors.ezq_collector", "ui.app_window"):
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001 - 어떤 오류든 그대로 보여 줌
            problems.append(f"불러오기 실패: {name} ({type(exc).__name__}: {exc})")
    if not problems:
        try:
            import tkinter
            root = tkinter.Tk()  # tcl 폴더가 빠지면 여기서 실패함
            root.withdraw()
            root.destroy()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Tcl/Tk 시작 실패 ({exc})")
    return problems


def main() -> int:
    print(f"꾸러미 폴더: {ROOT}")
    print(f"파이썬: {sys.version.split()[0]} / {sys.executable}")
    problems = check_files() + check_imports()
    if not problems:
        print("[OK] 파일이 모두 있고, 프로그램을 불러올 수 있습니다.")
        return 0
    print(f"[문제 {len(problems)}건]")
    for line in problems[:SHOW_MAX]:
        print("  " + line)
    if len(problems) > SHOW_MAX:
        print(f"  ... 외 {len(problems) - SHOW_MAX}건")
    print("꾸러미를 옮기는 중 파일이 지워졌거나 바뀌었습니다. 압축 파일을 다시 풀거나 다시 반입하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
