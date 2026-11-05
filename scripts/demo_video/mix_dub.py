"""더빙 합치기: 무음성 영상 + 문장별 음성 + 배경음악 → 완성 mp4 (용량 상한 확인 포함).

Mac에서 Qwen3-TTS로 문장마다 음성을 만든 뒤 실행합니다. Python 표준 라이브러리와 ffmpeg만 씁니다.

사용:
  python mix_dub.py <무음성.mp4> <bgm.m4a> <narration.json> <음성 폴더> <출력.mp4> [--limit-mb 200]
  python mix_dub.py ... --fake-voice      # 음성 대신 낭독 길이만큼의 시험음으로 용량·타이밍 확인

음성 파일 이름: <음성 폴더>/s{장면:02d}_l{문장:02d}.wav  (예: s03_l02.wav)

용량을 지키는 방법
- 영상은 다시 인코딩하지 않고 그대로 복사합니다(-c:v copy). 영상 트랙 크기가 바뀌지 않습니다.
  편집 프로그램(iMovie·Final Cut 등)으로 다시 내보내면 비트레이트가 10Mbps 이상이 되어 200MB를 넘을 수 있습니다.
- 소리는 AAC 192kbps 스테레오 하나로 섞습니다. 5분이면 약 7MB입니다.
- 끝나면 파일 크기를 재서 상한(기본 200MB)을 넘으면 실패로 끝냅니다. 95MB를 넘으면 GitHub에 올릴 수 없다고 알려 줍니다.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

MAX_TEMPO = 1.1      # 문장이 구간보다 길면 이 배속까지 올림
SYLLABLE_RATE = 4.3  # 측정한 낭독 속도(초당 음절), 시험음 길이 계산용
BGM_GAIN = 0.55      # 음성 없을 때 배경음악 크기
AUDIO_BITRATE = "192k"


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def voice_name(item):
    return f"s{item['scene']:02d}_l{item['line']:02d}.wav"


def make_fake_voices(lines, folder):
    """낭독 예상 길이만큼 말소리 대역의 시험음을 만듦 (용량·타이밍 점검용)."""
    os.makedirs(folder, exist_ok=True)
    for it in lines:
        syl = sum(1 for ch in it["text"] if not ch.isspace() and ch not in ".,?!·'\"")
        dur = max(1.0, syl / SYLLABLE_RATE)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"anoisesrc=d={dur:.2f}:c=pink:a=0.25:r=48000",
             "-af", "bandpass=f=900:width_type=h:w=1800,tremolo=f=4:d=0.7",
             "-ac", "1", os.path.join(folder, voice_name(it))], check=True)


def build_filter(lines, durations):
    """음성마다 시작 시각에 놓고(필요하면 1.1배까지 빠르게), 음성 동안 배경음악을 줄여 섞음."""
    parts, labels, warnings = [], [], []
    for i, (it, dur) in enumerate(zip(lines, durations)):
        slot = it["end"] - it["start"]
        chain = f"[{i + 2}:a]aresample=48000,aformat=channel_layouts=stereo"
        if dur > slot:
            tempo = dur / slot
            if tempo > MAX_TEMPO:
                warnings.append(f"장면 {it['scene']}-{it['line']}: 음성 {dur:.2f}초 > 구간 {slot:.2f}초 "
                                f"({tempo:.2f}배 필요, {MAX_TEMPO}배까지만 올림)")
                tempo = MAX_TEMPO
            chain += f",atempo={tempo:.4f}"
        ms = int(round(it["start"] * 1000))
        chain += f",adelay={ms}|{ms}[v{i}]"
        parts.append(chain)
        labels.append(f"[v{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,asplit=2[vo][sc]")
    parts.append(f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={BGM_GAIN}[bg]")
    parts.append("[bg][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck]")
    parts.append("[duck][vo]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
    return ";\n".join(parts), warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("bgm")
    ap.add_argument("narration")
    ap.add_argument("voice_dir")
    ap.add_argument("output")
    ap.add_argument("--limit-mb", type=float, default=200.0)
    ap.add_argument("--fake-voice", action="store_true")
    a = ap.parse_args()

    with open(a.narration, encoding="utf-8") as f:
        lines = json.load(f)
    if a.fake_voice:
        make_fake_voices(lines, a.voice_dir)

    missing = [voice_name(it) for it in lines if not os.path.exists(os.path.join(a.voice_dir, voice_name(it)))]
    if missing:
        sys.exit("음성 파일이 없습니다: " + ", ".join(missing))
    paths = [os.path.join(a.voice_dir, voice_name(it)) for it in lines]
    durations = [probe_duration(p) for p in paths]
    video_dur = probe_duration(a.video)

    graph, warnings = build_filter(lines, durations)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(graph)
        graph_file = tf.name
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", a.video, "-i", a.bgm]
    for p in paths:
        cmd += ["-i", p]
    cmd += ["-/filter_complex", graph_file, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000",
            "-t", f"{video_dur:.3f}", "-movflags", "+faststart", a.output]
    try:
        subprocess.run(cmd, check=True)
    finally:
        os.remove(graph_file)

    for w in warnings:
        print("주의:", w)
    size_mb = os.path.getsize(a.output) / 1024 / 1024
    print(f"완성: {a.output}  {size_mb:.1f}MB, {probe_duration(a.output):.1f}초 (상한 {a.limit_mb:.0f}MB)")
    if size_mb > 95:
        print("주의: 95MB를 넘어 GitHub 저장소에는 올릴 수 없습니다.")
    if size_mb > a.limit_mb:
        sys.exit(f"실패: {size_mb:.1f}MB로 상한 {a.limit_mb:.0f}MB를 넘었습니다.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
