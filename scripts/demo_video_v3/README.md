# 시연영상 v3 만들기

v2(`scripts/demo_video_v2/`)와 같은 방식·화면 구성·목소리(Supertonic F2)·배경음악입니다. 바뀐 점:

- 11.6 보완 장면 2개 추가: **07 문서 본문 판정**(요청 문서 등록 / 애매한 문서 확인 필요 / 통보 공문 제외, `제외된 쪽지·문서` 창), **10 기간 업무**(시작일 입력, 달력 여러 칸 표시, 쪽지의 `~` 기간, 반복 기간의 다음 기간)
- 05 장면 끝 문장: `제외된 쪽지` → `제외된 쪽지·문서`
- 개발 과정·검증 수치는 넣지 않음 (쪽지 판별 모션의 '실제 쪽지 20건 기준… 오등록 0건' 문구를 일반 설명으로 바꿈)
- 목표: 5분 이내, 파일 200MB 이하 (1080p, v2와 같은 crf 20·음성 192kbps). 결과: 4분 1초, 약 83MB (GitHub 파일 한도 100MB 이하)

작업 폴더는 저장소 밖 `C:\tc\video4`입니다.

```bash
# 1) 가상 데이터와 앱 사본 (실행한 날 기준 상대 날짜)
python scripts/demo_video/make_demo_data.py C:/tc/video4
# 2) 장면 캡처 52장 (앱 창이 몇 분 동안 뜨고 닫힘. 앱 창만 찍음)
python scripts/demo_video_v3/shoot_v3.py C:/tc/video4
# 3) 문장 목록 (어림 시간)
python scripts/demo_video_v3/script_v3.py draft C:/tc/video4
# 4) 더빙: v2와 같은 문장은 v2 음성을 새 번호로 복사하고, 새·바뀐 문장만 만듦
<supertonic .venv>/Scripts/python scripts/demo_video/gen_voice_supertonic.py C:/tc/video4/narration_v3.json C:/tc/video4/voice --voice F2 --only 5-6,7-1,7-2,7-3,7-4,10-1,10-2,10-3,10-4
# 5) 음성 길이로 시간 확정 → remotion/public/timeline.json, 캡처·음성 복사
python scripts/demo_video_v3/script_v3.py timeline C:/tc/video4
# 6) 배경음악 (timeline 길이 + 3초)
python scripts/demo_video/make_bgm.py <초> C:/tc/video4/remotion/public/bgm.wav
# 7) 렌더링 (remotion 폴더를 C:/tc/video4/remotion 에 두고 node_modules 준비)
cd C:/tc/video4/remotion
npx remotion render ./src/index.ts DemoV3 out/demo_v3.mp4 --gl=angle --crf=20 --audio-codec=aac --audio-bitrate=192k \
  --browser-executable="C:/Program Files/Google/Chrome/Application/chrome.exe"
```

- 캡처 단계는 `make_demo_data.py`가 만든 시연용 사본(`demo_app`)과 가상 DB만 씁니다. 사용자의 `todo_data.db`는 건드리지 않습니다.
- 회사 PC 보안 프로그램이 PNG·wav 등을 지울 수 있습니다. 캡처가 사라졌으면 1)~2)부터 다시 합니다.
- 음성 파일 번호는 `sNN_lNN.wav`(장면-문장)입니다. 장면을 넣거나 빼면 기존 음성을 새 번호로 옮겨야 다시 만들지 않아도 됩니다.
