"""프로그램 버전 (한 곳에서만 관리).

창 제목·사용 안내·꾸러미 이름이 모두 이 값을 씁니다. 예전에는 config.json의 "version"을 썼는데,
config.json은 사용자가 새 버전으로 옮길 때 그대로 가져가는 파일이라 옛 번호가 계속 떴습니다.

버전을 올릴 때: 이 값과 config.json의 "version"을 같이 고치고 CHANGELOG.md 맨 위에 항목을 추가합니다
(tests/test_version.py가 셋이 같은지 확인). 올린 뒤 `git tag v<버전>`으로 표시합니다.
"""

APP_VERSION = "1.5.1"
