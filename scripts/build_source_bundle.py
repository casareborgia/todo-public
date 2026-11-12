"""소스 실행 꾸러미 만들기: 소스 폴더 + 동봉 파이썬(runtime) → dist\\TodoCalendar_Source

파이썬이 설치되지 않은 PC(내부망)에서도 소스(.py)를 그대로 실행할 수 있게,
이 스크립트를 돌린 파이썬 본체를 runtime 폴더에 복사해 넣는다.
앱이 표준 라이브러리만 쓰므로 인터넷도 pip도 필요 없다.

- runtime\\python312._pth 로 동봉 파이썬을 격리한다. 대상 PC의 환경 변수(PYTHONPATH 등)나
  레지스트리, 다른 파이썬 설치의 영향을 받지 않는다.
- bundle_manifest.json 에 모든 파일의 크기·SHA-256을 적어 둔다. 반입 중 보안 프로그램이
  파일을 지웠는지 scripts\\verify_bundle.py(check.bat)로 확인한다.
- 꾸러미의 비트 수는 이 스크립트를 돌린 파이썬을 따른다(32비트 PC용은 32비트 파이썬으로 실행).

사용: python scripts/build_source_bundle.py [출력 폴더]
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_dist_config  # noqa: E402

sys.path.insert(0, ROOT)
from core.version import APP_VERSION  # noqa: E402

APP_DIRS = ["core", "collectors", "parsers", "ui"]
APP_FILES = ["main.py", "run.bat", "실행하기.bat", "check.bat", "README.md", "CHANGELOG.md"]
MANIFEST = "bundle_manifest.json"

# 표준 라이브러리 중 실행에 필요 없는 큰 폴더 (테스트·개발 도구·pip)
LIB_SKIP_DIRS = {
    "site-packages", "test", "tests", "idlelib", "idle_test", "ensurepip", "venv",
    "lib2to3", "turtledemo", "pydoc_data", "__pycache__",
}
# 파이썬 자체 테스트용 확장 모듈
DLL_SKIP_PREFIXES = ("_test", "_ctypes_test")
TCL_SKIP_DIRS = {"demos", "__pycache__"}


SOURCE_BUNDLE_NOTE = """[소스 실행본 안내]
  - 이 폴더는 소스코드(.py)와 파이썬 본체(runtime 폴더)를 함께 담은 실행본입니다.
    Python을 설치하지 않아도 됩니다.
  - 실행: 폴더 안의 run.bat (또는 실행하기.bat)을 실행합니다.
    아래 안내에서 "TodoCalendar_Portable.exe 를 실행"이라고 된 부분은 run.bat 실행으로 읽어 주세요.
  - 실행이 안 되면 check.bat 을 실행해 빠진 파일이 있는지 확인합니다.
    (옮기는 중 보안 프로그램이 파일을 지웠다면 목록이 나옵니다.)
  - runtime 폴더는 지우거나 옮기지 마세요.

"""


def _copy_tree(src: str, dst: str, skip_dirs=frozenset(), skip_file=lambda name: False) -> None:
    for cur, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        out = os.path.join(dst, os.path.relpath(cur, src))
        os.makedirs(out, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".pyo")) or skip_file(name):
                continue
            shutil.copy2(os.path.join(cur, name), os.path.join(out, name))


def copy_runtime(dst: str) -> None:
    home = sys.base_prefix
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    os.makedirs(dst)
    for name in os.listdir(home):
        # python.exe, pythonw.exe, python3.dll, python312.dll, vcruntime140*.dll
        if name.lower().endswith((".exe", ".dll")):
            shutil.copy2(os.path.join(home, name), os.path.join(dst, name))
    _copy_tree(os.path.join(home, "DLLs"), os.path.join(dst, "DLLs"),
               skip_file=lambda n: n.startswith(DLL_SKIP_PREFIXES))
    _copy_tree(os.path.join(home, "Lib"), os.path.join(dst, "Lib"), skip_dirs=LIB_SKIP_DIRS)
    _copy_tree(os.path.join(home, "tcl"), os.path.join(dst, "tcl"), skip_dirs=TCL_SKIP_DIRS)
    # ._pth 파일이 있으면 파이썬은 여기 적힌 경로만 쓴다(격리 모드).
    # '..' = 꾸러미 폴더(main.py, core, ui … 가 있는 곳)
    with open(os.path.join(dst, f"python{ver}._pth"), "w", encoding="ascii", newline="\r\n") as f:
        f.write("Lib\nDLLs\n..\n")


def copy_app(dst: str) -> None:
    for d in APP_DIRS:
        _copy_tree(os.path.join(ROOT, d), os.path.join(dst, d), skip_dirs={"__pycache__"})
    for name in APP_FILES:
        shutil.copy2(os.path.join(ROOT, name), os.path.join(dst, name))
    os.makedirs(os.path.join(dst, "scripts"), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "scripts", "verify_bundle.py"), os.path.join(dst, "scripts"))
    # 깨끗한 config.json, 사용안내.txt, 빈 scan_inbox (개인 todo_data.db는 넣지 않음)
    make_dist_config.main(os.path.join(ROOT, "config.json"), dst)
    # 사용 안내는 exe 실행본 기준이므로, 소스 실행본의 실행 방법을 맨 위에 덧붙임
    guide = os.path.join(dst, "사용안내.txt")
    with open(guide, "r", encoding="utf-8-sig") as f:
        body = f.read()
    with open(guide, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(SOURCE_BUNDLE_NOTE + body)


def write_manifest(dst: str) -> int:
    files = {}
    for cur, dirs, names in os.walk(dst):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            path = os.path.join(cur, name)
            rel = os.path.relpath(path, dst).replace("\\", "/")
            if rel == MANIFEST:
                continue
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            files[rel] = [os.path.getsize(path), digest]
    info = {
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "bits": struct.calcsize("P") * 8,
        "files": files,
    }
    with open(os.path.join(dst, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=0, sort_keys=True)
    return len(files)


def main() -> int:
    dst = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "TodoCalendar_Source")
    if os.path.exists(dst):
        if not os.path.isfile(os.path.join(dst, MANIFEST)):
            print(f"[ERROR] 출력 폴더가 이미 있고 이전 꾸러미가 아닙니다: {dst}")
            return 1
        shutil.rmtree(dst)
    os.makedirs(dst)
    bits = struct.calcsize("P") * 8
    print(f"v{APP_VERSION}, Python {sys.version.split()[0]} ({bits}-bit) -> {dst}")

    print("[1/4] 소스 복사...")
    copy_app(dst)
    print("[2/4] 파이썬 본체(runtime) 복사...")
    copy_runtime(os.path.join(dst, "runtime"))
    print("[3/4] 파일 목록(bundle_manifest.json) 기록...")
    count = write_manifest(dst)
    print(f"[4/4] 동봉 파이썬으로 점검 ({count}개 파일)...")
    # 대상 PC처럼 PATH에 파이썬이 없는 환경으로 실행. -B: 꾸러미에 __pycache__를 남기지 않음
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")}
    result = subprocess.run(
        [os.path.join(dst, "runtime", "python.exe"), "-B", os.path.join(dst, "scripts", "verify_bundle.py")],
        env=env,
    )
    if result.returncode != 0:
        print("[ERROR] 점검 실패. 꾸러미를 쓰지 마세요.")
        return 1
    print(f"완료: {dst}\\run.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
