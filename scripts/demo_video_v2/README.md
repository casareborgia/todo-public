# 시연영상 v2 만들기

작업 폴더는 저장소 밖 `C:\tc\video3`입니다. 캡처, 음성, `node_modules`, 렌더링 결과가 여기에 생깁니다.

```bash
# 1) 가상 데이터와 앱 사본 (실행한 날 기준 상대 날짜)
python scripts/demo_video/make_demo_data.py C:\tc\video3
# 2) 장면 캡처 46장 (앱 창이 몇 분 동안 뜨고 닫힘. 앱 창만 찍음)
python scripts/demo_video_v2/shoot_v2.py C:\tc\video3
# 3) 문장 목록 (어림 시간)
python scripts/demo_video_v2/script_v2.py draft C:\tc\video3
# 4) 더빙: Supertonic 3, 목소리 F2 (C:\tc\video3\tts\.venv 에 pip install supertonic)
C:\tc\video3\tts\.venv\Scripts\python scripts/demo_video/gen_voice_supertonic.py C:\tc\video3\narration_v2.json C:\tc\video3\voice --voice F2
# 5) 음성 길이로 시간 확정 → remotion/public/timeline.json, 캡처·음성 복사
python scripts/demo_video_v2/script_v2.py timeline C:\tc\video3
# 6) 배경음악 (timeline 길이 + 3초)
python scripts/demo_video/make_bgm.py <초> C:\tc\video3\remotion\public\bgm.wav
# 7) 렌더링 (remotion 폴더를 C:\tc\video3\remotion 에 두고 npm install)
cd C:\tc\video3\remotion
npx remotion render ./src/index.ts DemoV2 out/demo_v2.mp4 --gl=angle --crf=20 --audio-codec=aac --audio-bitrate=192k ^
  --browser-executable="C:/Program Files/Google/Chrome/Application/chrome.exe"
```

- 회사망에서는 Remotion이 렌더링용 Chrome을 내려받지 못합니다(인증서 검사). 그래서 설치된 Chrome을 `--browser-executable`로 지정합니다.
- 설명 음성과 배경음악은 Remotion이 렌더링할 때 함께 넣습니다(`src/Demo.tsx`). v1처럼 `mix_dub.py`로 따로 합칠 필요가 없습니다.
- 대본과 화면 지정은 `script_v2.py`의 `SCRIPT`에 있습니다. 문장을 고친 뒤에는 4)~7)을 다시 하고, 그 문장만 음성을 다시 만들려면 `--only 5-4 --force`를 씁니다.
- 모션그래픽(도입 카드 쌓기, 전체 구조 흐름도, 쪽지 판별, 같은 일 합치기, 반복 업무)은 `remotion/src/scenes/Motion.tsx`에 있는 2D 장면입니다(`spring`·`interpolate`·SVG). 움직임은 프레임 번호로만 정해지므로 같은 프레임이면 늘 같은 그림이 나옵니다.
- 문장 번호가 바뀌면(문장을 빼거나 넣으면) `voice/` 파일 이름도 새 번호에 맞춰 옮겨야 기존 더빙을 그대로 씁니다. 바뀐 문장만 `--only`로 다시 만듭니다.
