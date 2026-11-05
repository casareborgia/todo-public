"""시연영상 배경음악: 샘플 없이 합성한 잔잔한 음악 (영상 길이에 맞춤).

사용: python make_bgm.py <길이(초)> <출력 wav>
기존 쪽지 수집 영상(C:/tc/video/capture/bgm.py)과 같은 방식. numpy 필요(영상 제작용, 앱에는 쓰지 않음).
"""
import sys
import wave
import numpy as np

SR, BPM = 44100, 96
DUR = float(sys.argv[1])
t = np.arange(int(SR * DUR)) / SR
beat = 60 / BPM
bar = beat * 4


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


# Cmaj7 - Am7 - Fmaj7 - G6 (one bar each)
CHORDS = [[48, 55, 59, 64], [45, 52, 55, 60], [41, 48, 52, 57], [43, 50, 55, 59]]
ARP = [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 67]]

L = np.zeros_like(t)
R = np.zeros_like(t)
nbars = int(DUR / bar) + 1
for b in range(nbars):
    start = b * bar
    ch = CHORDS[b % 4]
    i0, i1 = int(start * SR), min(len(t), int((start + bar + 0.6) * SR))
    if i0 >= len(t):
        break
    tt = t[i0:i1] - start
    env = np.minimum(1, tt / 0.6) * np.exp(-np.maximum(0, tt - bar) * 4)  # soft swell, release into next bar
    for j, n in enumerate(ch):
        f = midi(n)
        pad = (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt + 0.5)
               + 0.12 * np.sin(2 * np.pi * 3 * f * tt)) * env * 0.05
        pan = 0.35 + 0.1 * j
        L[i0:i1] += pad * (1 - pan)
        R[i0:i1] += pad * pan
    # bass: root on beats 1 and 3
    for k in (0, 2):
        s = start + k * beat
        j0, j1 = int(s * SR), min(len(t), int((s + beat * 1.8) * SR))
        if j0 >= len(t):
            continue
        u = t[j0:j1] - s
        f = midi(ch[0] - 12)
        v = np.sin(2 * np.pi * f * u) * np.exp(-u * 2.2) * np.minimum(1, u / 0.01) * 0.16
        L[j0:j1] += v
        R[j0:j1] += v
    # arpeggio plucks on eighth notes, entering after the intro
    if b >= 2:
        arp = ARP[b % 4]
        for k in range(8):
            s = start + k * beat / 2
            j0, j1 = int(s * SR), min(len(t), int((s + 0.9) * SR))
            if j0 >= len(t):
                continue
            u = t[j0:j1] - s
            n = arp[[0, 1, 2, 3, 2, 1, 3, 2][k]] + 12
            f = midi(n)
            v = (np.sin(2 * np.pi * f * u) + 0.25 * np.sin(2 * np.pi * 2 * f * u)) * np.exp(-u * 6) \
                * np.minimum(1, u / 0.004) * 0.045
            pan = 0.3 if k % 2 else 0.7
            L[j0:j1] += v * (1 - pan)
            R[j0:j1] += v * pan
    # soft hi-hat on off-beats (filtered noise) from bar 4
    if b >= 4:
        rng = np.random.default_rng(b)
        for k in range(4):
            s = start + k * beat + beat / 2
            j0, j1 = int(s * SR), min(len(t), int((s + 0.06) * SR))
            if j0 >= len(t):
                continue
            u = t[j0:j1] - s
            nz = rng.standard_normal(j1 - j0)
            nz = np.diff(np.concatenate([[0], nz]))  # crude high-pass
            v = nz * np.exp(-u * 60) * 0.012
            L[j0:j1] += v
            R[j0:j1] += v

# simple stereo echo for space
d = int(beat * 0.75 * SR)
for src, dst in ((L, R), (R, L)):
    dst[d:] += src[:-d] * 0.22

# fade in/out, normalize
fade = np.minimum(1, t / 2.0) * np.minimum(1, (DUR - t) / 3.0)
L *= fade
R *= fade
peak = max(np.abs(L).max(), np.abs(R).max())
L, R = L / peak * 0.8, R / peak * 0.8
out = (np.stack([L, R], axis=1) * 32767).astype(np.int16)
with wave.open(sys.argv[2], "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(out.tobytes())
print("ok", out.shape[0] / SR)
