"""문장별 더빙 음성 만들기 (Supertonic 3): narration.json → voice/sNN_lNN.wav

2026-11-03 사용자 선택: Supertonic 3, 기본 목소리 F2 (여성 2번).
Qwen3-TTS는 문장마다 음색이 흔들리고, 목소리 복제는 문장 끝이 끊겨서 바꿈.
같은 입력이면 늘 같은 소리가 나와 문장 사이 목소리가 일정합니다.

실행 환경: ~/coding/supertonic-tts/.venv  (pip install supertonic, 모델은 처음 실행 때 내려받음)

사용:
  <supertonic .venv>/bin/python gen_voice_supertonic.py <narration.json> <음성 폴더> [--voice F2] [--only 3-2,5-4] [--force]

- 숫자·영문 약어는 읽는 소리대로 바꿔 넣습니다(gen_voice.py의 SPOKEN과 같음). 자막(text)은 바꾸지 않습니다.
- 앞뒤 무음을 자르되 끝에 0.15초 여유를 남겨 말끝이 잘리지 않게 합니다.
"""
import argparse
import json
import os
import sys

import numpy as np

STEPS = 16       # 합성 단계 (기본 8, 높을수록 품질↑)
SPEED = 1.0

SPOKEN = [
    ("15초", "십오 초"), ("3초", "삼 초"), ("30분", "삼십 분"), ("2시간", "두 시간"),
    ("오후 2시", "오후 두 시"), ("4주", "사 주"), ("PDF", "피디에프"), ("PC", "피씨"),
    # 시연영상 v2 (순서 중요: "20건"·"19건"을 "0건"보다 먼저)
    ("20건", "이십 건"), ("19건", "십구 건"), ("0건", "영 건"), ("7일", "칠 일"), ("100여 번", "백여 번"),
    ("268개", "이백육십팔 개"), ("F1", "에프 원"),
]


def spoken(text):
    for a, b in SPOKEN:
        text = text.replace(a, b)
    return text.replace("'", "")


def trim(a, sr, thr=0.005, head=0.05, tail=0.15):
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    return a[max(0, idx[0] - int(sr * head)):min(len(a), idx[-1] + int(sr * tail))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("narration")
    ap.add_argument("voice_dir")
    ap.add_argument("--voice", default="F2")
    ap.add_argument("--only", default="", help="장면-문장 목록, 예: 3-2,5-4")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    with open(a.narration, encoding="utf-8") as f:
        lines = json.load(f)
    if a.only:
        want = {tuple(int(x) for x in k.split("-")) for k in a.only.split(",")}
        lines = [it for it in lines if (it["scene"], it["line"]) in want]
    os.makedirs(a.voice_dir, exist_ok=True)

    from supertonic import TTS
    tts = TTS()
    style = tts.get_voice_style(a.voice)
    sr = tts.sample_rate

    for it in lines:
        path = os.path.join(a.voice_dir, f"s{it['scene']:02d}_l{it['line']:02d}.wav")
        if os.path.exists(path) and not a.force:
            continue
        wav, _ = tts.synthesize(spoken(it["text"]), voice_style=style, lang="ko",
                                total_steps=STEPS, speed=SPEED)
        audio = trim(np.asarray(wav, dtype=np.float32).reshape(-1), sr)
        tts.save_audio(audio, path)
        slot = it["end"] - it["start"]
        mark = "  << 구간 초과" if len(audio) / sr > slot else ""
        print(f"{it['scene']}-{it['line']}: {len(audio) / sr:.2f}s / 구간 {slot:.2f}s{mark}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
