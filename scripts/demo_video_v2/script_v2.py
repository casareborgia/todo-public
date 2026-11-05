"""시연영상 v2 대본·화면 지정 → 문장 목록과 Remotion 시간표.

사용:
  python script_v2.py draft    [작업 폴더]   → <작업>/narration_v2.json (어림 시간, 음성 만들기용)
  python script_v2.py timeline [작업 폴더]   → 음성 길이로 시간 확정: <작업>/narration_v2.json,
                                               <작업>/remotion/public/timeline.json (+ 캡처·음성 복사)
작업 폴더 기본값: C:\\tc\\video3  (shots/, voice/ 가 있어야 함)

- 문장마다 화면에 머무는 시간 = 음성 길이 + 1.0초, 장면이 바뀔 때 0.8초 더 (음성이 없으면 음절 수 ÷ 4.5 + 1.5초)
- 화면 종류: card(글자 화면), motion(2D 모션그래픽), app(앱 캡처, 확대·강조), mock(업무관리시스템 재현 화면),
  fit(창 크기 3가지)
- 숫자·표현은 참가신청서·README에 있는 것만 씁니다.
"""

import json
import os
import re
import shutil
import sys
import wave

BASE = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else r"C:\tc\video3")
SHOTS = os.path.join(BASE, "shots")
VOICE = os.path.join(BASE, "voice")
PUBLIC = os.path.join(BASE, "remotion", "public")
FPS = 30
PAD, MIN_DUR, RATE, EST_PAD = 1.0, 2.8, 4.5, 1.5
SCENE_GAP = 0.8  # 장면이 바뀔 때 앞 문장 뒤에 두는 여유 (전환 효과)
LEAD = 0.6  # 영상 맨 앞 무음
FAKE = "※ 화면의 업무 데이터는 가상입니다"
FAKE_MSG = "※ 쪽지 창은 시연용 재현 화면이며, 업무 데이터는 가상입니다"
FAKE_WM = "※ 업무관리시스템 화면은 시연용 재현 화면이며, 업무 데이터는 가상입니다"


def card(cid):
    return {"k": "card", "id": cid}


def motion(mid):
    return {"k": "motion", "id": mid}


def app(img, zoom=None, hl=(), tag=None, note=FAKE):
    return {"k": "app", "img": img, "zoom": zoom, "hl": list(hl), "tag": tag, "note": note}


SCRIPT = [
    (1, "도입", [
        ("공문, 쪽지, 그리고 업무관리시스템 배부까지.", motion("desk")),
        ("들어오는 모든 업무를 캘린더 하나로 모읍니다.", card("title")),
    ]),
    (2, "문제", [
        ("하루에도 수십 건씩 오는 문서와 쪽지.", card("problem")),
        ("그 속에 묻힌 업무 하나를 한 달 반 뒤에야 발견한 적이 있습니다.", card("problem2")),
        ("그래서 내부망에서 쓰는 업무 누락 방지 도구를 만들었습니다.", card("problem3")),
    ]),
    (3, "전체 구조", [
        ("업무는 세 갈래로 들어옵니다. 감시 폴더의 공문 파일, 메신저 쪽지, 업무관리시스템의 배부 문서입니다.", motion("flow")),
        ("들어온 업무는 업무인지 먼저 판단한 뒤 캘린더에 오릅니다.", motion("flow")),
        ("모든 과정은 내 PC 안에서만 이루어지고, 외부와 통신하지 않습니다.", motion("flow")),
    ]),
    (4, "출근 브리핑과 메인 화면", [
        ("아침에 켜면 출근 브리핑이 먼저 뜹니다.", app("01_briefing", tag="출근 브리핑")),
        ("기한이 지난 업무와 오늘 마감, 이번 주 마감을 한눈에 보여 줍니다.", app("01_briefing", tag="출근 브리핑")),
        ("메인 화면은 월간 캘린더이고, 라벨 색으로 출처와 종류를 나눕니다.", app("02_main_month", hl=["legend"])),
        ("감시 폴더에 공문 PDF가 저장되면 제목과 기한, 문서번호를 찾아 바로 등록합니다.",
         app("05_auto_detail", zoom="detail", hl=["meta"], tag="공문 PDF → 자동 등록")),
    ]),
    (5, "쪽지 자동 등록과 과탐 방지", [
        ("메신저 쪽지는 창을 열기만 하면,", app("07_ezq_msg", note=FAKE_MSG, tag="쪽지 창 열기")),
        ("기한과 함께 캘린더에 올라갑니다.", app("07_ezq_selected", zoom="daylist", hl=["sel_row"], tag="쪽지 → 3초 안에 등록")),
        ("하지만 쪽지가 모두 업무는 아닙니다.", motion("sort")),
        ("단순 인사와 시스템 알림은 스스로 걸러내고, 기한이 애매한 쪽지만 확인 필요로 분리해 오등록 없이 정확하게 관리합니다.",
         motion("sort")),
        ("애매한 쪽지는 확인 필요에 모아 두고, 캘린더와 알림에는 띄우지 않습니다.",
         app("v2_review_queue", tag="❔ 확인 필요")),
        ("걸러 낸 쪽지는 제외된 쪽지에서 다시 보고, 필요하면 업무로 등록합니다.",
         app("v2_dismissed_msgs", tag="🧾 제외된 쪽지")),
    ]),
    (6, "업무관리시스템 배부 문서", [
        ("업무관리시스템의 배부 문서도 자동으로 등록합니다.", {"k": "mock", "id": "list", "hl": False}),
        ("부서장이 문서를 나에게 배부하면 결재시행 목록에 나타납니다.", {"k": "mock", "id": "list", "hl": True}),
        ("목록을 열어 두기만 하면 3초 뒤 업무로 등록됩니다.",
         app("v2_list_registered", zoom="daylist", hl=["sel_row"], tag="배부 → 3초 뒤 등록")),
        ("목록에는 처리기한이 없어, 기한은 일단 추정으로 둡니다.",
         app("v2_list_registered", zoom="detail", hl=["meta"], tag="기한 추정")),
        ("문서를 열면 본문의 회신기한을 읽어 기한을 확정합니다.",
         app("v2_doc_opened", zoom="detail", hl=["meta"], tag="문서 열기 → 기한 확정")),
        ("업무관리자로 받은 문서는 확인 필요로 보내, 직접 고르게 합니다.", app("v2_review_queue", tag="❔ 확인 필요")),
        ("목록과 문서는 읽기만 하고, 문서를 열거나 결재하지 않습니다.", {"k": "mock", "id": "readonly", "hl": False}),
    ]),
    (7, "같은 일 합치기", [
        ("같은 일을 동료 쪽지로 먼저 받았다면 어떻게 될까요?", motion("merge")),
        ("새로 만들지 않고, 먼저 있던 업무에 배부 출처를 붙여 하나로 합칩니다.", motion("merge")),
        ("출처에는 쪽지와 업무관리시스템 문서번호가 함께 남습니다.",
         app("v2_merged", zoom="detail", hl=["meta"], tag="쪽지 + 배부 = 업무 하나")),
    ]),
    (8, "반복 업무", [
        ("매주 반복하는 업무는 주황색 정기 라벨로 표시되고,", motion("recur")),
        ("앞으로의 날짜가 캘린더에 미리 보여, 다음 일정까지 한눈에 계획할 수 있습니다.",
         app("v2_recur_preview", zoom="month", tag="↻ 반복 미리보기")),
    ]),
    (9, "손에 익는 편의", [
        ("한 줄로 쓰면 기한과 시각을 읽어 바로 등록하고,",
         app("09_quick_preview", zoom="quick", hl=["quick_entry"], tag="⚡ 빠른 입력")),
        ("끌어다 놓으면 기한이 바뀝니다.", app("10_drag", zoom="cal", tag="끌어다 놓기")),
        ("여러 업무를 골라 한 번에 지울 수 있고,", app("v2_multi_delete", tag="여러 건 삭제")),
        ("F1 키로 단축키 안내를 엽니다.", app("v2_shortcuts", tag="F1 단축키")),
        ("창 크기에 맞춰, 캘린더가 보여 줄 업무 수를 스스로 맞춥니다.", {"k": "fit"}),
    ]),
    (10, "마무리", [
        ("외부 통신 없이, 표준 라이브러리만으로, 설치 없이 실행됩니다.", card("security")),
        ("받은 업무, 이제 놓치지 않습니다.", card("end")),
    ]),
]


def syllables(text):
    return len(re.findall(r"[가-힣0-9A-Za-z]", text))


def wav_seconds(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def resolve(view, rects):
    """app 화면의 확대·강조 이름 → 캡처 픽셀 위치 [x, y, w, h]."""
    if view["k"] != "app":
        return view
    v = dict(view)
    img = v["img"]

    def r(key):
        return rects.get(f"{img}.{key}")

    v["zoom"] = r(v["zoom"]) if v.get("zoom") else None
    v["hl"] = [x for x in (r(k) for k in v["hl"]) if x]
    path = os.path.join(SHOTS, img + ".png")
    with open(path, "rb") as f:
        head = f.read(24)
    v["size"] = [int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")]
    return v


def build(use_voice):
    rects = json.load(open(os.path.join(SHOTS, "rects.json"), encoding="utf-8")) if os.path.exists(
        os.path.join(SHOTS, "rects.json")) else {}
    lines, t = [], LEAD
    for n, (scene, name, items) in enumerate(SCRIPT):
        if n:
            lines[-1]["end"] = round(lines[-1]["end"] + SCENE_GAP, 2)
            t += SCENE_GAP
        for i, (text, view) in enumerate(items, 1):
            wav = os.path.join(VOICE, f"s{scene:02d}_l{i:02d}.wav")
            if use_voice and os.path.exists(wav):
                voice = wav_seconds(wav)
                dur = max(MIN_DUR, voice + PAD)
            else:
                voice = None
                dur = syllables(text) / RATE + EST_PAD
            lines.append({"scene": scene, "scene_name": name, "line": i, "start": round(t, 2),
                          "end": round(t + dur, 2), "text": text, "voice": round(voice, 2) if voice else None,
                          "view": resolve(view, rects) if use_voice else view})
            t += dur
    return lines, t


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "draft"
    lines, total = build(mode == "timeline")
    narr = [{k: ln[k] for k in ("scene", "line", "start", "end", "text")} for ln in lines]
    json.dump(narr, open(os.path.join(BASE, "narration_v2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(lines)}문장, {total:.1f}초 ({int(total // 60)}분 {total % 60:.0f}초)")
    if mode != "timeline":
        return
    os.makedirs(os.path.join(PUBLIC, "shots"), exist_ok=True)
    os.makedirs(os.path.join(PUBLIC, "voice"), exist_ok=True)
    used = {ln["view"]["img"] for ln in lines if ln["view"]["k"] == "app"} | {
        "v2_fit_min", "v2_fit_def", "v2_fit_max", "02_main_month"}
    for img in used:
        shutil.copy(os.path.join(SHOTS, img + ".png"), os.path.join(PUBLIC, "shots", img + ".png"))
    # 지난 대본의 음성이 남지 않게 비우고 다시 복사
    for old in os.listdir(os.path.join(PUBLIC, "voice")):
        if old.endswith(".wav"):
            os.remove(os.path.join(PUBLIC, "voice", old))
    for ln in lines:
        name = f"s{ln['scene']:02d}_l{ln['line']:02d}.wav"
        if os.path.exists(os.path.join(VOICE, name)):
            shutil.copy(os.path.join(VOICE, name), os.path.join(PUBLIC, "voice", name))
            ln["wav"] = f"voice/{name}"
    end_card = 3.0
    tl = {"fps": FPS, "duration": round(total + end_card, 2), "lines": lines,
          "scenes": [{"scene": s, "name": n, "start": min(ln["start"] for ln in lines if ln["scene"] == s),
                      "end": max(ln["end"] for ln in lines if ln["scene"] == s)} for s, n, _ in SCRIPT]}
    tl["scenes"][-1]["end"] = tl["duration"]
    json.dump(tl, open(os.path.join(PUBLIC, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    over = [ln for ln in lines if ln["voice"] is None]
    print("timeline.json 저장. 음성 없는 문장:", len(over))


if __name__ == "__main__":
    main()
