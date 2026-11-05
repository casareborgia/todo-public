"""Windows 로그인/출근 시 프로그램 자동 실행 등록 및 해제 헬퍼.

관리자 권한(UAC)이 필요 없는 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
레지스트리 키를 사용하여 사내 보안 환경에서도 안전하게 시작프로그램 등록을 처리합니다.
"""

import os
import sys

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "InternalTodoCalendarManager"


def get_launch_command() -> str:
    """현재 실행 환경(.py 스크립트 또는 패키징된 .exe)에 맞는 실행 명령어 생성."""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return f'"{exe_path}" --startup'
    else:
        python_exe = sys.executable
        # 로그인 시 콘솔 창이 뜨지 않도록 같은 폴더의 pythonw.exe가 있으면 우선 사용
        pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.basename(python_exe).lower() == "python.exe" and os.path.isfile(pythonw_exe):
            python_exe = pythonw_exe
        main_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
        return f'"{python_exe}" "{main_script}" --startup'


def is_startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ
        ) as key:
            val, _ = winreg.QueryValueEx(key, APP_REG_NAME)
            return bool(val)
    except Exception:
        return False


def set_startup_enabled(enable: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                cmd = get_launch_command()
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False
