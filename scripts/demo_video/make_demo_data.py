"""기능 전반 시연영상용 가상 데이터 만들기.

사용: python make_demo_data.py [작업 폴더, 기본 C:\\tc\\video2]

- <작업 폴더>\\demo_app   : git archive HEAD로 복사한 앱 + 빈 DB에 가상 업무를 넣은 시연용 사본
- <작업 폴더>\\demo_files : 감시 폴더·파일 불러오기·점검에 쓸 가상 공문 PDF, 쪽지 저장 txt
- 날짜는 모두 실행한 날 기준 상대 날짜라 언제 찍어도 화면이 자연스럽습니다.
- 사용자의 실제 todo_data.db·config.json은 건드리지 않습니다.
- 이름(홍길동·김철수·이영희)과 부서·문서번호는 모두 가상입니다.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"C:\tc\video2")
APP = os.path.join(BASE, "demo_app")
FILES = os.path.join(BASE, "demo_files")
WEEK = "월화수목금토일"
T = date.today()


def d(n: int) -> date:
    return T + timedelta(days=n)


def ymd(x: date) -> str:
    return x.strftime("%Y-%m-%d")


def short(x: date) -> str:  # 'YY.M.D(요일)
    return f"'{x:%y}.{x.month}.{x.day}({WEEK[x.weekday()]})"


def long(x: date) -> str:  # 2026. 11. 6.(금)
    return f"{x.year}. {x.month}. {x.day}.({WEEK[x.weekday()]})"


def friday() -> date:
    return T + timedelta(days=(4 - T.weekday()) % 7)


# ---------------------------------------------------------------- 1. 앱 사본
def copy_app() -> None:
    if os.path.exists(APP):
        shutil.rmtree(APP, ignore_errors=True)
    os.makedirs(APP, exist_ok=True)
    archive = os.path.join(BASE, "app.zip")
    subprocess.run(["git", "-C", REPO, "archive", "--format=zip", "-o", archive, "HEAD"], check=True)
    with zipfile.ZipFile(archive) as z:  # 한글 파일 이름 때문에 tar 대신 zip
        z.extractall(APP)
    os.remove(archive)
    for p in glob.glob(os.path.join(APP, "scan_inbox", "*")):
        os.remove(p)
    cfg = json.load(open(os.path.join(APP, "config.json"), encoding="utf-8"))
    cfg.update(
        watch_folders=[],
        show_startup_briefing=False,  # 촬영 스크립트가 원하는 때에 직접 띄움
        reminder_check_seconds=86400,  # 촬영 중 알림 창이 저절로 뜨지 않게 (알림 장면은 직접 띄움)
        ezq_auto_collect=True,
        ezq_window_class=r"WindowsForms10\.Window\.8\..*",  # 시연용 재현 쪽지 창(WinForms)을 읽도록
    )
    json.dump(cfg, open(os.path.join(APP, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 2. 가상 업무
def seed_db() -> None:
    sys.path.insert(0, APP)
    from core.database import DatabaseManager
    from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus

    db = DatabaseManager(os.path.join(APP, "todo_data.db"))
    P, M, N = TaskSource.PORTAL, TaskSource.MESSENGER, TaskSource.MANUAL
    rows = [
        # 제목, 기한, 시각, 출처, 라벨, 우선순위, 출처정보, 원문, 옵션
        ("정보보안 서약서 제출", d(-3), "18:00", P, "PORTAL", 2, "총무팀-0488",
         "[공문] 정보보안 서약서 제출 요청\n시행 총무팀-0488\n전 직원은 정보보안 서약서를 작성하여 제출하여 주시기 바랍니다.", {}),
        ("10월 출장비 정산", d(-5), "18:00", N, "MANUAL", 3, "직접 등록", "10월 출장비 정산 서류 제출", {"done": True}),
        ("[긴급] 국정감사 요구자료 제출", d(0), "16:00", P, "URGENT", 1, "기획팀-1219",
         "[공문] 국정감사 요구자료 제출 (긴급)\n시행 기획팀-1219\n요구자료를 금일중 제출하여 주시기 바랍니다.", {}),
        ("회의실 비품 현황 회신", d(0), "17:00", M, "MESSENGER", 2, "쪽지 · 이영희",
         "회의실 비품 현황 오늘까지 회신 부탁드립니다.", {}),
        ("협력사 계약 검토 의견 회신", d(1), "18:00", P, "PORTAL", 2, "총무팀-0495",
         "[공문] 협력사 계약서 검토 의견 회신 요청\n시행 총무팀-0495", {}),
        ("청사 소방훈련 참여 인원 회신", d(2), "15:00", P, "PORTAL", 2, "총무팀-0502",
         "[공문] 청사 소방훈련 실시 및 참여 인원 회신 요청\n시행 총무팀-0502", {"new": True}),
        ("부서 워크숍 장소 섭외", d(3), "18:00", N, "MANUAL", 2, "직접 등록", "워크숍 후보지 3곳 비교", {}),
        ("주간업무실적 제출", friday(), "17:00", N, "ROUTINE", 2, "정기 업무",
         "매주 금요일 주간업무실적 제출", {"recur": RecurrenceType.WEEKLY}),
        ("하반기 예산 집행실적 제출", d(9), "18:00", P, "PORTAL", 2, "기획팀-1240",
         "[공문] 하반기 예산 집행실적 제출 요청\n시행 기획팀-1240", {}),
        ("연말 교육계획 취합", d(12), "16:00", M, "MESSENGER", 2, "쪽지 · 김철수",
         "연말 교육계획 다음 달 초까지 취합 부탁드립니다.", {}),
        ("개인정보 보호 교육 이수", d(16), "18:00", N, "MANUAL", 3, "직접 등록", "온라인 교육 2시간", {}),
    ]
    for title, due, tm, src, label, prio, ref, desc, opt in rows:
        item = TaskItem(title=title, description=desc, source=src, source_ref=ref, due_date=ymd(due), due_time=tm,
                        label=label, priority=prio, recurrence=opt.get("recur", RecurrenceType.NONE),
                        is_new_scan=bool(opt.get("new")))
        tid, _ = db.add_task(item, skip_if_duplicate=False)
        if opt.get("done"):
            db.set_task_status(tid, TaskStatus.COMPLETED)

    # 처리 실적 창(📊 실적)의 최근 4주 막대가 비지 않도록, 지난 3주 동안 끝낸 가상 업무
    # (제목, 등록일, 마감일, 완료일, 출처) — 하나는 기한을 넘겨 완료
    history = [
        ("9월 회의록 정리", -22, -19, -20, P), ("부서 비품 신청", -21, -18, -18, M),
        ("보안점검 체크리스트 회신", -20, -17, -17, P), ("주간 업무보고 작성", -15, -12, -12, N),
        ("청렴 교육 이수", -15, -11, -13, P), ("출장 계획 결재 요청", -14, -10, -11, M),
        ("민원 처리 결과 회신", -13, -9, -8, P), ("위원회 자료 취합", -9, -6, -6, M),
        ("예산 전용 검토 의견", -8, -5, -5, P), ("홍보물 시안 확인", -8, -4, -4, M),
        ("부서 행사 결과 보고", -7, -3, -3, N),
    ]
    updates = []
    for title, created, due, done, src in history:
        label = {P: "PORTAL", M: "MESSENGER"}.get(src, "MANUAL")
        tid, _ = db.add_task(TaskItem(title=title, source=src, due_date=ymd(d(due)), label=label,
                                      source_ref="가상 이력"), skip_if_duplicate=False)
        updates.append((TaskStatus.COMPLETED, ymd(d(created)) + " 09:00:00", ymd(d(done)) + " 16:30:00", tid))
    conn = db._get_conn()  # 등록일·완료일을 과거로 (앱 화면으로는 바꿀 수 없는 값)
    try:
        conn.executemany("UPDATE tasks SET status=?, created_at=?, completed_at=? WHERE id=?", updates)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 3. 가상 파일
GONGMUN = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>공문</title>
<style>body{{font-family:"Malgun Gothic",sans-serif;font-size:13pt;margin:40px}}h1{{text-align:center;font-size:20pt}}
table{{border-collapse:collapse}}td{{border:1px solid #333;padding:4px 8px}}.foot{{margin-top:40px;font-size:11pt}}</style>
</head><body>
<h1>가상공사</h1>
<p>수신&nbsp;&nbsp;수신자 참조</p>
<p>(경유)</p>
<p>제목&nbsp;&nbsp;{title}</p>
<p>1. 관련: {rel}</p>
<p>2. {body}</p>
<p>&nbsp;&nbsp;가. 제출기한: {due}</p>
<table><tr><td>구분</td><td>항목</td><td>비고</td></tr><tr><td>1</td><td>{item}</td><td>필수</td></tr></table>
<p>붙임&nbsp;&nbsp;{attach} 1부.&nbsp;&nbsp;끝.</p>
<p style="text-align:center;font-size:16pt">{chief}</p>
<p class="foot">시행 {no} ({sent})&nbsp;&nbsp;접수 정보보호팀-3321 ({sent})</p>
</body></html>"""

CHROME_GLOBS = [
    os.path.expanduser(r"~\.cache\hyperframes\chrome\*\*\*\chrome-headless-shell.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def chrome() -> str:
    for g in CHROME_GLOBS:
        hits = glob.glob(g)
        if hits:
            return hits[0]
    raise SystemExit("PDF 인쇄용 Chrome/Edge를 찾지 못했습니다.")


def html_to_pdf(html: str, pdf: str) -> None:
    src = pdf[:-4] + ".html"
    open(src, "w", encoding="utf-8").write(html)
    subprocess.run([chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", "file:///" + src.replace("\\", "/")],
                   check=True, capture_output=True)
    os.remove(src)


def messenger(sender: str, sent: str, title: str, body: str) -> tuple:
    stamp = sent.replace(":", "").replace(" ", "_")  # 2026-11-02_091024
    name = f"{sender}-{stamp}.txt"
    text = f"송신:{sender}\n시간:{sent}:000\n제목:{title}\n내용:{body}\n"
    return name, text


def make_files() -> dict:
    if os.path.exists(FILES):
        shutil.rmtree(FILES)
    for sub in ("auto", "import", "inspect"):
        os.makedirs(os.path.join(FILES, sub))
    sent = f"{T.year}. {T.month}. {T.day}."
    # 감시 폴더에 넣을 공문: 'YY.M.D(요일) 형식 기한
    html_to_pdf(GONGMUN.format(
        title="2027년 주요업무계획 작성 제출 요청", rel="기획팀-1201", item="주요업무계획",
        body=f"위 호와 관련하여 부서별 2027년 주요업무계획을 붙임 양식에 작성하여 {short(d(4))}까지 제출하여 주시기 바랍니다.",
        due=short(d(4)), attach="작성 양식", chief="기획팀장", no="기획팀-1234", sent=sent),
        os.path.join(FILES, "auto", "2027년_주요업무계획_제출요청.pdf"))
    # 불러오기·점검용 공문: 2026. 11. 9.(월) 형식 기한
    for sub in ("import", "inspect"):
        html_to_pdf(GONGMUN.format(
            title="사무용품 수요조사 회신 요청", rel="총무팀-0510", item="사무용품 수요",
            body=f"위 호와 관련하여 부서별 사무용품 수요를 조사하여 {long(d(7))}까지 회신하여 주시기 바랍니다.",
            due=long(d(7)), attach="수요조사표", chief="총무팀장", no="총무팀-0517", sent=sent),
            os.path.join(FILES, sub, "사무용품_수요조사_회신요청.pdf"))
    today = ymd(T)
    msgs = {
        "auto": [messenger("홍길동", f"{today} 09:10:24", "출장 계획서 송부 요청",
                           "기획팀 홍길동입니다.\n내일 오전까지 출장 계획서 송부 부탁드립니다.")],
        "import": [
            messenger("이영희", f"{today} 08:52:40", "교육 수요조사",
                      "금요일까지 부서 교육 수요조사 결과 회신 부탁드립니다."),
            messenger("김철수", f"{today} 09:35:12", "Re: 출장 계획서", "네, 알겠습니다."),
        ],
    }
    for sub, items in msgs.items():
        for name, text in items:
            open(os.path.join(FILES, sub, name), "w", encoding="utf-8").write(text)
    return {"today": today}


if __name__ == "__main__":
    os.makedirs(BASE, exist_ok=True)
    copy_app()
    seed_db()
    info = make_files()
    print("demo app:", APP)
    print("demo files:", FILES)
    for root, _dirs, files in os.walk(FILES):
        for f in files:
            print("  ", os.path.relpath(os.path.join(root, f), FILES))
    print("today:", info["today"], "friday:", ymd(friday()))
