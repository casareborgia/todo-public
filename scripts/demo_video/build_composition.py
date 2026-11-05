"""시연영상 구성(HyperFrames index.html)과 문장별 시간표(narration.json) 만들기.

사용: python build_composition.py [작업 폴더, 기본 C:\\tc\\video2]
- 입력: <작업 폴더>\\shots\\*.png (shoot_all.py 결과)
- 출력: <작업 폴더>\\project\\index.html, <작업 폴더>\\narration.json
- 문장마다 화면에 머무는 시간 = 글자 수(음절) ÷ 4.5 + 1.5초 여유 (Mac에서 더빙할 때 음성이 구간 안에 들어가도록)
- 설명 음성·배경음악은 넣지 않습니다 (배경음악은 make_bgm.py로 따로).
"""

import html
import json
import os
import re
import shutil
import struct
import sys

BASE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"C:\tc\video2")
SHOTS = os.path.join(BASE, "shots")
PROJ = os.path.join(BASE, "project")
OLD = r"C:\tc\video\project\assets"  # 기존 영상의 글꼴·gsap 재사용
RATE, PAD, GAP = 4.5, 1.5, 0.6
FW, FH, FL, FT = 1380, 911, 270, 34  # 앱 화면 틀 (1602x1057 캡처를 같은 비율로)
FAKE = "※ 화면의 업무 데이터는 가상입니다"
FAKE_MSG = "※ 쪽지 창은 시연용 재현 화면이며, 업무 데이터는 가상입니다"


def png_size(name):
    with open(os.path.join(SHOTS, name + ".png"), "rb") as f:
        return struct.unpack(">II", f.read(24)[16:24])


def syllables(text):
    return len(re.findall(r"[가-힣0-9A-Za-z]", text))


def app(img, zoom=None, hl=(), tag=None, note=FAKE):
    return {"kind": "app", "img": img, "zoom": zoom, "hl": list(hl), "tag": tag, "note": note}


def pop(img, bg="02_main_month", hl=(), tag=None, note=FAKE, at=None):
    return {"kind": "pop", "img": img, "bg": bg, "hl": list(hl), "tag": tag, "note": note, "at": at}


def pair(img, msg, zoom=None, hl=(), tag=None):
    return {"kind": "pair", "img": img, "msg": msg, "zoom": zoom, "hl": list(hl), "tag": tag, "note": FAKE_MSG}


def card(name):
    return {"kind": "card", "name": name}


# 화면 좌표는 캡처 이미지 픽셀 기준 (x, y, w, h).
# 앱 창 위치는 shoot_all.py가 찍을 때 남긴 rects.json에서 읽음 (빠른 입력 줄 등으로 화면 배치가 바뀌어도 맞도록)
_RX = {}


def _rx():
    if not _RX:
        _RX.update(json.load(open(os.path.join(SHOTS, "rects.json"), encoding="utf-8")))
    return _RX


def R(shot, key):
    return list(_rx()[f"{shot}.{key}"])


def CELL(shot, which, h=None):
    """그 장면에서 'date.<which>' 날짜 칸. h를 주면 칸 윗부분만."""
    r = R(shot, "cell." + _rx()["date." + which])
    return r if h is None else [r[0], r[1], r[2], h]


def LINES(shot, key, n):
    """상세 칸 글자 n줄 (key: meta=제목·기한 등 요약, text=원문)."""
    r = R(shot, key)
    ls = _rx()[f"{shot}.{'meta_ls' if key == 'meta' else 'text_ls'}"]
    return [r[0] - 4, r[1] - 2, r[2], ls * n + 6]


def UNION(*rs):
    x0, y0 = min(r[0] for r in rs), min(r[1] for r in rs)
    x1, y1 = max(r[0] + r[2] for r in rs), max(r[1] + r[3] for r in rs)
    return [x0, y0, x1 - x0, y1 - y0]


def GROW(r, px, py=None):
    py = px if py is None else py
    return [r[0] - px, r[1] - py, r[2] + 2 * px, r[3] + 2 * py]


def ZOOM_DETAIL(shot):
    return GROW(R(shot, "detail"), 20, 30)


def ZOOM_TOP(bottom):
    """창 위쪽(상태 줄 ~ bottom)을 왼쪽 2/3 너비로 확대."""
    return [0, 30, 1000, bottom - 30]


# (장면 번호, [(문장, 화면), ...]) — 화면 좌표가 rects.json에 의존하므로 함수로 만듦
def make_script():
    ezq_cell = CELL("07_ezq_registered", "ezq")
    ezq_zoom = ZOOM_TOP(ezq_cell[1] + ezq_cell[3] + 20)
    cal_top = R("02_main_month", "legend")[1] - 12
    quick_cell = CELL("09_quick_added", "quick")
    drop_cell = CELL("10_drag_after", "drag_dst")
    return [
        (1, [("공문과 쪽지로 오는 업무, 이제 놓치지 않습니다.", card("title"))]),
        (2, [("하루에도 수십 건씩 오는 공문과 쪽지.", card("problem")),
             ("그 속에 묻힌 업무 하나를 한 달 반 뒤에야 발견한 적이 있습니다.", card("problem2")),
             ("그래서 내부망에서 쓸 수 있는 업무 누락 방지 도구를 만들었습니다.", card("problem3")),
             ("쪽지 창을 열기만 하면, 기한과 함께 캘린더에 자동으로 등록됩니다.",
              pair("07_ezq_registered", "07_ezq_msg", zoom=ezq_zoom, hl=[ezq_cell, R("07_ezq_registered", "status")],
                   tag=("쪽지 → 3초 안에 자동 등록", "green", (300, 120))))]),
        (3, [("아침에 프로그램을 켜면 출근 브리핑이 먼저 뜹니다.", pop("01_briefing", tag=("출근 브리핑", "blue", (300, 40)))),
             ("기한이 지난 업무, 오늘 마감, 이번 주 마감, 새로 들어온 업무를 한눈에 보여 줍니다.",
              pop("01_briefing", hl=[(22, 137, 232, 80), (262, 137, 224, 80), (493, 137, 201, 80), (702, 137, 231, 80)]))]),
        (4, [("메인 화면은 월간 캘린더입니다.", app("02_main_month")),
             ("라벨 색으로 공문, 메신저, 긴급, 직접 등록, 반복 업무를 구분합니다.",
              app("02_main_month", zoom=[20, cal_top, 980, 360], hl=[R("02_main_month", "legend")])),
             ("기한이 지났는데 끝내지 않은 업무는 오늘 칸으로 계속 이월되어 경고합니다.",
              app("03_detail", zoom=[20, cal_top, 1560, 560], hl=[CELL("03_detail", "today"), R("03_detail", "sel_row")],
                  tag=("3일 지난 업무 → 오늘 칸에 이월", "red", (300, 110)))),
             ("업무를 고르면 원문을 바로 볼 수 있습니다.",
              app("03_detail", zoom=ZOOM_DETAIL("03_detail"), hl=[LINES("03_detail", "text", 3)]))]),
        (5, [("처음 한 번만 설정하면 됩니다.", pop("04_folder_settings", tag=("감시 폴더 설정", "blue", (300, 60)))),
             ("쪽지 저장 폴더와 공문 다운로드 폴더를 감시 폴더로 추가하면,",
              pop("04_folder_settings", hl=[(18, 132, 916, 52), (28, 468, 680, 64)])),
             ("파일이 저장될 때마다 15초 안에 업무로 등록됩니다.",
              app("05_auto_after", zoom=ZOOM_TOP(CELL("05_auto_after", "pdf")[1] + 100),
                  hl=[R("05_auto_after", "status"), CELL("05_auto_after", "msg", 85), CELL("05_auto_after", "pdf", 85)],
                  tag=("파일 저장 → 자동 등록", "green", (330, 760)))),
             ("공문 PDF에서는 제목, 처리기한, 시행번호를 자동으로 찾아냅니다.",
              app("05_auto_detail", zoom=ZOOM_DETAIL("05_auto_detail"), hl=[LINES("05_auto_detail", "meta", 4)])),
             ("쪽지의 '내일 오전까지'는 받은 시각을 기준으로 실제 날짜로 바꿉니다.",
              app("05_auto_detail_msg", zoom=ZOOM_DETAIL("05_auto_detail_msg"),
                  hl=[LINES("05_auto_detail_msg", "meta", 2)]))]),
        (6, [("이미 받아 둔 파일은 여러 개를 한 번에 불러올 수 있습니다.", pop("06_import_result")),
             ("파일마다 새 업무인지, 업무가 아닌지 결과를 보여 줍니다.",
              pop("06_import_result", hl=[(18, 116, 1025, 76)]))]),
        (7, [("메신저 쪽지는 파일로 저장하지 않아도 됩니다.",
              pair("05_auto_after", "07_ezq_msg", tag=("EzQ 받은쪽지 창", "blue", (1200, 70)))),
             ("쪽지 창을 열면 3초 안에 본문을 읽어 업무인지 판단하고,",
              pair("07_ezq_registered", "07_ezq_msg", zoom=ezq_zoom, hl=[R("07_ezq_registered", "status"), ezq_cell],
                   tag=("3초 안에 자동 등록", "green", (300, 120)))),
             ("'다음 주 월요일 오전까지' 같은 표현을 실제 날짜로 바꿔 등록합니다.",
              app("07_ezq_selected", zoom=ZOOM_DETAIL("07_ezq_selected"), hl=[LINES("07_ezq_selected", "meta", 2)],
                  note=FAKE_MSG)),
             ("'확인했습니다' 같은 단순 답장은 걸러 냅니다.",
              pair("07_ezq_after_reply", "07_ezq_reply_msg", tag=("단순 답장 → 등록 안 함", "red", (1200, 470))))]),
        (8, [("화면 내용을 복사한 뒤 클립보드 스캔 한 번이면 등록됩니다.",
              pop("08_clipboard_msg", bg="08_clipboard_after", tag=("클립보드 스캔", "green", (300, 60)))),
             ("붙여넣기 창에 직접 붙여 넣어도 됩니다.", pop("08_paste_dialog", bg="08_clipboard_after"))]),
        (9, [("직접 추가할 때는 빠른 입력 칸에 한 줄로 쓰면 됩니다.",
              app("09_quick_preview", zoom=[0, 110, 1000, 150],
                  hl=[R("09_quick_preview", "quick_entry"), R("09_quick_preview", "quick_preview")],
                  tag=("⚡ 빠른 입력", "blue", (300, 360)))),
             ("'모레 오후 2시까지'를 읽어, 기한과 시각을 맞춰 바로 등록합니다.",
              app("09_quick_added", zoom=[0, 110, 1000, quick_cell[1] + quick_cell[3] + 20 - 110],
                  hl=[R("09_quick_added", "quick_preview"), quick_cell],
                  tag=("Enter → 바로 등록", "green", (300, 110)))),
             ("매주 내는 실적 보고처럼 반복되는 업무는,",
              pop("09_new_task", bg="10_recurring_before", hl=[(146, 224, 562, 30), (146, 304, 184, 30)])),
             ("완료하면 다음 일정이 자동으로 생깁니다.",
              pop("10_recurring_msg", bg="10_recurring_after", tag=("다음 주 금요일 자동 생성", "amber", (300, 60))))]),
        (10, [("캘린더에서 일정을 우클릭하면 편집, 완료, 미루기, 삭제를 바로 고를 수 있습니다.",
               app("10_ctx_menu", zoom=GROW(UNION(R("10_ctx_menu", "menu"), CELL("10_ctx_menu", "drag_src")), 80, 50),
                   hl=[R("10_ctx_menu", "menu")], tag=("우클릭 메뉴", "blue", (300, 110)))),
              ("일정을 끌어 다른 날짜에 놓으면 기한이 바뀝니다.",
               app("10_drag", zoom=[20, cal_top, 980, 360],
                   hl=[CELL("10_drag", "drag_dst")], tag=("끌어다 놓기 → 기한 변경", "amber", (300, 110)))),
              ("바뀐 기한은 위쪽에 바로 알려 줍니다.",
               app("10_drag_after", zoom=[0, 110, 1000, drop_cell[1] + drop_cell[3] + 20 - 110],
                   hl=[R("10_drag_after", "quick_preview"), drop_cell]))]),
        (11, [("목록 뷰에서는 마감이 급한 순서대로 보고,",
               app("11_agenda", zoom=[20, R("11_agenda", "agenda")[1] - 40, 975, 560])),
              ("검색과 필터로 원하는 업무만 골라 볼 수 있습니다.",
               app("12_filter", zoom=[0, 110, 1000, 360],
                   hl=[GROW(UNION(R("12_filter", "status_cb"), R("12_filter", "label_cb")), 6, 3)])),
              ("실적 창에서는 이번 주 처리 건수와 기한 내 처리율, 최근 4주 추이를 봅니다.",
               pop("12_stats", bg="15_main_final", hl=[(480, 216, 108, 110), (20, 400, 568, 140)],
                   tag=("📊 처리 실적", "green", (300, 60))))]),
        (12, [("마감이 다가오면 알림이 뜹니다.", pop("13_reminder", tag=("마감 알림", "amber", (300, 60)))),
              ("바로 완료하거나 30분, 2시간 뒤, 내일 아침으로 미룰 수 있습니다.",
               pop("13_reminder", hl=[(14, 476, 532, 46)]))]),
        (13, [("글자가 이상하게 읽히는 파일은 점검 기능으로, 등록 없이 인식 결과만 확인할 수 있습니다.",
               pop("14_inspect", hl=[(28, 370, 590, 72)], tag=("파일 점검", "blue", (260, 40))))]),
        (14, [("인터넷 연결 없이 내 PC 안에서만 동작하고, 파이썬 표준 라이브러리만 사용합니다.", card("security")),
              ("메신저를 고치지 않고 화면에 보이는 본문만 읽습니다.", card("security")),
              ("관리자 권한도, 설치도 필요 없이 폴더째 복사해 바로 씁니다.", card("security2")),
              ("단어 규칙으로 판단하기 때문에, 안내성 쪽지도 업무로 등록될 수 있습니다.", card("limit"))]),
        (15, [("공문과 쪽지 속 업무, 이제 놓치지 않습니다.", card("closing"))]),
    ]


def build_timeline():
    t = 0.8
    lines = []
    for scene, items in make_script():
        for i, (text, view) in enumerate(items):
            dur = round(syllables(text) / RATE + PAD, 2)
            lines.append({"scene": scene, "line": i + 1, "start": round(t, 2), "end": round(t + dur, 2),
                          "text": text, "view": view})
            t += dur
        t += GAP
    return lines, round(t + 1.2, 2)


# ---------------------------------------------------------------- HTML 조각
def frame_xy(rect, k):
    x, y, w, h = rect
    return x * k, y * k, w * k, h * k


def zoom_tf(rect, iw):
    if not rect:
        return {"x": 0, "y": 0, "scale": 1}
    k = FW / iw
    rx, ry, rw, rh = frame_xy(rect, k)
    s = max(1, min(FW / rw, FH / rh, 2.4))
    tx = min(0, max(FW - s * FW, FW / 2 - s * (rx + rw / 2)))
    ty = min(0, max(FH - s * FH, FH / 2 - s * (ry + rh / 2)))
    return {"x": round(tx, 1), "y": round(ty, 1), "scale": round(s, 3)}


def hl_divs(lid, rects, k):
    out = []
    for j, r in enumerate(rects):
        x, y, w, h = frame_xy(r, k)
        out.append(f'<div id="{lid}h{j}" class="hl" style="left:{x - 4:.1f}px;top:{y - 4:.1f}px;'
                   f'width:{w + 8:.1f}px;height:{h + 8:.1f}px"></div>')
    return "".join(out)


def tag_div(lid, tag):
    if not tag:
        return ""
    text, color, (x, y) = tag
    return f'<div id="{lid}t" class="step {color}" style="left:{x}px;top:{y}px">{html.escape(text)}</div>'


def app_frame(lid, img, hl, extra_style=""):
    iw, _ = png_size(img)
    k = FW / iw
    return (f'<div class="frame" style="{extra_style}"><div id="{lid}z" class="zoom">'
            f'<img src="assets/shots/{img}.png" alt="" />{hl_divs(lid, hl, k)}</div></div>')


def popup(lid, img, hl, at=None, scale=None):
    w, h = png_size(img)
    s = min(1.0, 860 / h, 1500 / w)
    if scale:
        s = scale
    elif w < 700:
        s = min(1.45, 860 / h)  # 작은 메시지 상자는 키워서 읽기 쉽게
    pw, ph = w * s, h * s
    x, y = at if at else ((1920 - pw) / 2, (1000 - ph) / 2)
    rects = "".join(
        f'<div id="{lid}h{j}" class="hl" style="left:{r[0] * s - 4:.1f}px;top:{r[1] * s - 4:.1f}px;'
        f'width:{r[2] * s + 8:.1f}px;height:{r[3] * s + 8:.1f}px"></div>' for j, r in enumerate(hl))
    return (f'<div id="{lid}p" class="pop" style="left:{x:.0f}px;top:{y:.0f}px;width:{pw:.0f}px;height:{ph:.0f}px">'
            f'<img src="assets/shots/{img}.png" alt="" />{rects}</div>')


CARDS = {
    "title": """<div class="center"><div class="badge">사내 업무 캘린더 &amp; 투두 매니저 v1.2.0</div>
      <div class="title">공문과 쪽지 속 업무,<br/><em>놓치지 않게</em></div>
      <div class="subtitle">자동 수집 · 기한 추출 · 출근 브리핑 · 마감 알림</div></div>""",
    "problem": """<div class="pile-head"><h2>공문과 쪽지는<br/>매일 쌓입니다</h2><p>안내, 공지, 잡담 사이에 섞인 업무 지시</p>
      <div class="count"><span class="num">40</span><span>건 / 하루 받은 공문·쪽지</span></div></div>
      <div class="pile">
      <div class="note" style="top:0"><b>공문 · 총무팀</b><div>청사 소방훈련 실시 안내</div></div>
      <div class="note" style="top:118px"><b>쪽지 · 홍길동</b><div>회의 자료 공유드립니다</div></div>
      <div class="note key" style="top:236px"><b>공문 · 기획팀</b><div>주요업무계획 작성 제출 요청 (기한 있음)</div></div>
      <div class="note" style="top:354px"><b>쪽지 · 이영희</b><div>오늘 점심 같이 하실 분?</div></div>
      <div class="note" style="top:472px"><b>공문 · 정보보안팀</b><div>보안 업데이트 적용 공지</div></div>
      <div class="note" style="top:590px"><b>쪽지 · 김철수</b><div>내일 오전까지 송부 부탁드립니다</div></div>
      <div class="note" style="top:708px"><b>공문 · 인사팀</b><div>사내 교육 신청 안내</div></div></div>""",
    "security": """<div class="center"><div class="chips">
      <div class="chip"><i>●</i>인터넷 연결 없음</div><div class="chip"><i>●</i>표준 라이브러리만 사용</div></div>
      <div class="chips" style="margin-top:28px"><div class="chip"><i>●</i>메신저 수정 없음 · 화면 본문 읽기만</div></div>
      <div class="sec-note">외부 API·클라우드 호출 없음 · 타 프로세스 후킹·패킷 감청 없음</div></div>""",
    "security2": """<div class="center"><div class="chips">
      <div class="chip"><i>●</i>관리자 권한 불필요</div><div class="chip"><i>●</i>무설치 · 폴더째 복사</div></div>
      <div class="crop"><img src="assets/shots/15_main_final.png" alt="" /></div>
      <div class="sec-note">출근 시 자동실행 · EzQ 쪽지 수집 켜기/끄기</div></div>""",
    "limit": """<div class="center"><div class="limit-box"><div class="limit-h">알아 둘 점</div>
      <div class="limit-b">AI 없이 단어·문장 규칙으로 업무를 판단합니다.<br/>
      놓치는 것보다 잘못 들어오는 쪽이 낫다는 기준이라,<br/>안내성 쪽지도 등록될 수 있습니다. 필요 없으면 지우면 됩니다.</div></div></div>""",
    "closing": """<div class="center"><div class="closing">공문과 쪽지 속 업무,<br/><em>이제 놓치지 않습니다.</em></div>
      <div class="product">사내 개인용 투두리스트 &amp; 캘린더 v1.2.0</div></div>""",
}
CARDS["problem2"] = CARDS["problem"].replace('class="note key"', 'class="note key lost"') + \
    '<div class="missed">한 달 반 뒤에야 발견</div>'
CARDS["problem3"] = """<div class="center"><div class="badge">내부망 전용 · 업무 누락 방지 도구</div>
  <div class="title" style="font-size:88px">받은 업무를 <em>자동으로</em> 캘린더에</div>
  <div class="flow"><span>공문 PDF</span><span>쪽지 저장 txt</span><span>EzQ 쪽지 창</span><span>복사한 화면</span></div></div>"""


def view_key(v):
    name = {"problem2": "problem"}.get(v.get("name"), v.get("name"))  # 같은 카드에 강조만 더함
    return (v["kind"], v.get("img"), name, v.get("bg"), v.get("msg"))


def build_html(lines, total):
    secs, caps, js = [], [], []
    for n, ln in enumerate(lines):
        v, lid = ln["view"], f"L{n}"
        start, dur = ln["start"], ln["end"] - ln["start"]
        prev = lines[n - 1]["view"] if n else None
        nxt = lines[n + 1]["view"] if n + 1 < len(lines) else None
        # 다음 문장까지의 간격도 화면이 이어지도록 덮음
        vis_end = lines[n + 1]["start"] if nxt else total
        same_prev = prev is not None and view_key(prev) == view_key(v) and lines[n - 1]["scene"] == ln["scene"]
        same_next = nxt is not None and view_key(nxt) == view_key(v) and lines[n + 1]["scene"] == ln["scene"]
        body = ""
        if v["kind"] == "card":
            body = CARDS[v["name"]]
        elif v["kind"] == "app":
            body = app_frame(lid, v["img"], v["hl"])
        elif v["kind"] == "pop":
            body = app_frame(lid + "b", v["bg"], []) + '<div class="dim"></div>' + popup(lid, v["img"], v["hl"], v.get("at"))
        elif v["kind"] == "pair":
            body = app_frame(lid, v["img"], v["hl"]) + popup(lid + "m", v["msg"], [], at=(1290, 500), scale=1.0)
        body += tag_div(lid, v.get("tag"))
        if v.get("note"):
            body += f'<div class="footnote">{html.escape(v["note"])}</div>'
        secs.append(f'<section id="{lid}" class="clip" data-start="{start:.2f}" data-duration="{vis_end - start:.2f}" '
                    f'data-track-index="1">{body}</section>')
        caps.append(f'<div id="c{n}" class="cap clip" data-start="{start:.2f}" data-duration="{dur:.2f}" '
                    f'data-track-index="5"><span>{html.escape(ln["text"])}</span></div>')
        # 애니메이션
        if not same_prev:
            js.append(f'tl.fromTo("#{lid}",{{opacity:0}},{{opacity:1,duration:0.4,ease:"power2.out"}},{start:.2f});')
        if not same_next:
            js.append(f'tl.to("#{lid}",{{opacity:0,duration:0.35,ease:"power2.in"}},{vis_end - 0.35:.2f});')
        if v["kind"] in ("app", "pair"):
            iw, _ = png_size(v["img"])
            to = zoom_tf(v.get("zoom"), iw)
            fr = zoom_tf(prev.get("zoom"), iw) if same_prev and prev["kind"] in ("app", "pair") else \
                {"x": 0, "y": 0, "scale": 1}
            if to != fr:
                js.append(f'tl.fromTo("#{lid}z",{json.dumps(fr)},{{...{json.dumps(to)},duration:1.1,ease:"power2.inOut"}},'
                          f'{start + 0.25:.2f});')
            else:
                js.append(f'tl.set("#{lid}z",{json.dumps(to)},{start:.2f});')
        if v["kind"] == "pop":
            if not same_prev:
                js.append(f'tl.fromTo("#{lid}p",{{scale:0.94,y:20}},{{scale:1,y:0,duration:0.55,ease:"power3.out"}},'
                          f'{start + 0.1:.2f});')
        if v["kind"] == "pair":
            js.append(f'tl.fromTo("#{lid}mp",{{x:220,opacity:0}},{{x:0,opacity:1,duration:0.6,ease:"power3.out"}},'
                      f'{start + 0.2:.2f});')
        for j in range(len(v.get("hl", []))):
            at = start + 1.0 + 0.35 * j
            js.append(f'tl.fromTo("#{lid}h{j}",{{opacity:0,scale:1.12}},{{opacity:1,scale:1,duration:0.4,ease:"power3.out"}},{at:.2f});')
            js.append(f'tl.to("#{lid}h{j}",{{borderColor:"#fde047",duration:0.45,yoyo:true,repeat:3}},{at + 0.5:.2f});')
        if v.get("tag"):
            js.append(f'tl.fromTo("#{lid}t",{{y:24,opacity:0}},{{y:0,opacity:1,duration:0.5,ease:"power3.out"}},'
                      f'{start + 0.6:.2f});')
        if v["kind"] == "card":
            js.append(f'tl.fromTo("#{lid} .center > *, #{lid} .pile-head, #{lid} .note, #{lid} .missed",'
                      f'{{y:26,opacity:0}},{{y:0,opacity:1,duration:0.55,stagger:0.12,ease:"power3.out"}},{start + 0.15:.2f});'
                      if not same_prev else "")
            if v["name"] == "problem" and not same_prev:
                js.append(f'(()=>{{const c={{v:0}};tl.fromTo(c,{{v:0}},{{v:40,duration:2.2,ease:"power2.out",'
                          f'onUpdate:()=>{{document.querySelectorAll("#{lid} .num").forEach(e=>e.textContent=Math.round(c.v))}}}},'
                          f'{start + 0.4:.2f});}})();')
            if v["name"] == "problem2":
                js.append(f'tl.set("#{lid} .num",{{textContent:40}},{start:.2f});')
                js.append(f'tl.fromTo("#{lid} .lost",{{opacity:1}},{{opacity:0.25,duration:0.6}},{start + 0.8:.2f});')
                js.append(f'tl.fromTo("#{lid} .missed",{{scale:0.6,opacity:0}},{{scale:1,opacity:1,duration:0.45,'
                          f'ease:"back.out(2)"}},{start + 1.1:.2f});')
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "composition_template.html"), encoding="utf-8").read()
    return (tpl.replace("{{TOTAL}}", f"{total:.2f}").replace("{{SECTIONS}}", "\n".join(secs))
            .replace("{{CAPTIONS}}", "\n".join(caps)).replace("{{SCRIPT}}", "\n".join(j for j in js if j))
            .replace("{{FL}}", str(FL)).replace("{{FT}}", str(FT)).replace("{{FW}}", str(FW)).replace("{{FH}}", str(FH)))


def main():
    lines, total = build_timeline()
    os.makedirs(os.path.join(PROJ, "assets", "shots"), exist_ok=True)
    for sub in ("fonts", "vendor"):
        dst = os.path.join(PROJ, "assets", sub)
        if not os.path.exists(dst):
            shutil.copytree(os.path.join(OLD, sub), dst)
    for f in os.listdir(SHOTS):
        shutil.copy(os.path.join(SHOTS, f), os.path.join(PROJ, "assets", "shots", f))
    open(os.path.join(PROJ, "index.html"), "w", encoding="utf-8").write(build_html(lines, total))
    narr = [{k: ln[k] for k in ("scene", "line", "start", "end", "text")} for ln in lines]
    json.dump(narr, open(os.path.join(BASE, "narration.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"문장 {len(lines)}개, 전체 길이 {total:.1f}초 ({int(total // 60)}분 {total % 60:.0f}초)")
    for s in sorted({ln["scene"] for ln in lines}):
        ls = [ln for ln in lines if ln["scene"] == s]
        print(f"  장면 {s:2d}: {ls[0]['start']:6.1f} ~ {ls[-1]['end']:6.1f}")


if __name__ == "__main__":
    main()
