"""앱 화면에서 바꾼 설정을 config.json에 저장 (다른 항목은 그대로 유지)."""

import json
import os


def read_config_file(config_path: str) -> dict:
    """config.json 읽기. 메모장이 붙이는 BOM(utf-8-sig)과 예전 메모장의 ANSI(cp949) 저장도 읽음.

    JSON 형식이 깨졌으면 json.JSONDecodeError를 그대로 냄.
    """
    with open(config_path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("config.json의 최상위는 { } 객체여야 합니다")
    return data


def save_config_values(config_path: str, **values) -> None:
    """config.json의 일부 값만 바꿔 저장. 기존 파일이 깨져 있으면 덮어쓰지 않고 예외를 냄."""
    data = {}
    if os.path.isfile(config_path):
        data = read_config_file(config_path)
    data.update(values)
    # 저장 중 전원이 꺼져도 설정 파일이 반쯤 쓰인 채 남지 않도록 임시 파일에 쓴 뒤 교체
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, config_path)
