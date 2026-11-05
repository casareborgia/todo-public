"""문장별 더빙 음성 만들기 (Mac 전용, Qwen3-TTS): narration.json → voice/sNN_lNN.wav

※ 최종 더빙은 gen_voice_supertonic.py(Supertonic 3 F2)로 만들었습니다. 이 스크립트는 대안으로 남겨 둡니다.

기본: Qwen3-TTS 0.6B CustomVoice, 목소리 Sohee (2026-10-31 사용자 선택).
복제: --ref-audio/--ref-text를 주면 Qwen3-TTS 1.7B Base로 그 목소리를 복제합니다 (2026-11-03 사용자 녹음).
실행 환경: ~/coding/나만의 자비스/.venv-tts (mlx-audio), HF_HUB_OFFLINE=1

사용:
  HF_HUB_OFFLINE=1 <.venv-tts>/bin/python gen_voice.py <narration.json> <음성 폴더> [--only 3-2,5-4] [--force]
  ... --ref-audio my_voice.wav --ref-text "녹음 대본"      # 녹음한 목소리로 복제

- 문장마다 시드를 고정하고 temperature를 낮춰(0.3) 문장 사이 음색 차이를 줄입니다.
- 숫자·영문 약어는 자막과 달리 읽는 소리대로 바꿔 넣습니다(SPOKEN). 자막(text)은 바꾸지 않습니다.
- 앞뒤 무음을 잘라 저장합니다. 길이가 예상(초당 4.3음절)과 크게 다르면 다시 생성합니다(최대 3번).
- 이미 있는 파일은 건너뜁니다(--force로 다시 생성).
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import soundfile as sf

MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-bf16"
CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
TEMPERATURE = 0.3
SEED = 1234
VOICE = "sohee"
STYLE = "차분하고 또박또박한 발표 톤, 적당히 밝게"
SYLLABLE_RATE = 4.3
TRIES = 3

# 읽는 소리대로 바꿀 표현 (자막 → 음성 입력)
SPOKEN = [
    ("15초", "십오 초"), ("3초", "삼 초"), ("30분", "삼십 분"), ("2시간", "두 시간"),
    ("오후 2시", "오후 두 시"), ("4주", "사 주"), ("PDF", "피디에프"), ("PC", "피씨"),
]


def spoken(text):
    for a, b in SPOKEN:
        text = text.replace(a, b)
    return text.replace("'", "")


def syllables(text):
    return sum(1 for ch in text if not ch.isspace() and ch not in ".,?!·'\"")


def trim(a, sr, thr=0.01, pad=0.05):
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    s = max(0, idx[0] - int(sr * pad))
    e = min(len(a), idx[-1] + int(sr * pad))
    return a[s:e]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("narration")
    ap.add_argument("voice_dir")
    ap.add_argument("--only", default="", help="장면-문장 목록, 예: 3-2,5-4")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ref-audio", help="복제할 목소리 녹음 (wav)")
    ap.add_argument("--ref-text", help="녹음 대본 (녹음과 정확히 같아야 함)")
    a = ap.parse_args()

    with open(a.narration, encoding="utf-8") as f:
        lines = json.load(f)
    if a.only:
        want = {tuple(int(x) for x in k.split("-")) for k in a.only.split(",")}
        lines = [it for it in lines if (it["scene"], it["line"]) in want]
    os.makedirs(a.voice_dir, exist_ok=True)

    import mlx.core as mx
    from mlx_audio.tts.utils import load_model
    clone = bool(a.ref_audio)
    if clone and not a.ref_text:
        sys.exit("--ref-audio에는 --ref-text(녹음 대본)가 필요합니다.")
    model = load_model(CLONE_MODEL if clone else MODEL)
    voice_args = (dict(ref_audio=a.ref_audio, ref_text=a.ref_text) if clone
                  else dict(voice=VOICE, instruct=STYLE))
    sr = getattr(model, "sample_rate", 24000)

    for it in lines:
        path = os.path.join(a.voice_dir, f"s{it['scene']:02d}_l{it['line']:02d}.wav")
        if os.path.exists(path) and not a.force:
            continue
        text = spoken(it["text"])
        expect = syllables(it["text"]) / SYLLABLE_RATE
        slot = it["end"] - it["start"]
        best = None
        for n in range(TRIES):
            t = time.time()
            mx.random.seed(SEED + n)
            res = list(model.generate(text=text, lang_code="korean", temperature=TEMPERATURE,
                                      max_tokens=min(2048, len(text) * 6), stream=False, **voice_args))
            audio = trim(np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in res]), sr)
            dur = len(audio) / sr
            ok = 0.7 * expect <= dur <= 1.5 * expect
            # 정상 범위이면서 구간에 더 잘 맞는 것을 고름
            score = (0 if ok else 1, max(0.0, dur - slot))
            if best is None or score < best[0]:
                best = (score, audio, dur)
            print(f"{it['scene']}-{it['line']} try{n + 1}: {dur:.2f}s (예상 {expect:.2f}, 구간 {slot:.2f}) "
                  f"{time.time() - t:.1f}s", flush=True)
            if ok and dur <= slot * 1.1:
                break
        sf.write(path, best[1], sr)
        print(f"  저장 {os.path.basename(path)} {best[2]:.2f}s", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
