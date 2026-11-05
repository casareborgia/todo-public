"""업무관리시스템 문서 창의 본문 읽기 (Windows 전용).

업무관리시스템은 Edge IE 모드로 열리고, 문서 본문은 한컴 웹한글 기안기(WebHwpCtrl)가 canvas에 그림으로
그려서 화면 글자로는 읽을 수 없습니다 (내부망 점검 3차, 2026-11-03). 대신 기안기의 본문 꺼내기 기능
GetTextFile('TEXT')로 본문 글자를 받습니다 (점검 4차에서 확인). 문서를 바꾸거나 저장하지 않습니다.

- 창 찾기와 IE 화면 문서 받기는 EzQ 쪽지 창과 같은 방식(WM_HTML_GETOBJECT, collectors/ezq_window.py)
- 기안기 호출은 화면 안에서 짧은 식을 실행해서 함 (eval, 옛 IE 화면 방식이면 execScript)
- GetTextFile은 결과를 콜백으로 늦게 돌려주므로 두 번에 나눠 받음: 이번 확인에서 요청(REQ),
  다음 확인에서 결과(TEXT). 그래서 프로그램이 멈춰 기다리지 않음
Python 표준 라이브러리(ctypes)만 사용합니다.
"""

import ctypes
import re
import sys
from typing import Callable, List, NamedTuple, Optional, Tuple

from collectors import ezq_window as _ew
from parsers.workmgmt_list import ListRow, classify_row, parse_list_table

DEFAULT_WINDOW_CLASS = r"Chrome_WidgetWin_1|IEFrame"  # Edge 창 (IE 모드 탭이 이 창 안에 있음)

# 상태: TEXT(본문 받음) / REQ(꺼내기 요청함) / WAIT(결과 기다림) / NOCTRL(기안기 없음: 목록 화면 등)
#       ERR(기안기 오류) / NOJS(화면에서 식을 실행할 수 없음)
TEXT, REQ, WAIT, NOCTRL, ERR, NOJS = "TEXT", "REQ", "WAIT", "NOCTRL", "ERR", "NOJS"


class DocWindowResult(NamedTuple):
    hwnd: int
    title: str      # 창 제목 (문서번호·제목이 들어 있음)
    status: str
    text: str = ""  # TEXT면 본문, ERR면 오류 내용


# 화면(window)에서 실행할 식. 옛 IE 화면 방식(IE7 문서 모드)에서도 돌도록 ES3 문법만 씀.
# 1) 지난 확인에서 요청한 결과가 와 있으면 돌려줌  2) 요청 중이면 기다림(15초 지나면 다시 요청)
# 3) 이 화면이나 작은 화면(iframe) 안에서 GetTextFile을 가진 기안기를 찾아 꺼내기 요청
# 결과·요청 표시는 이 화면의 window.__todoTxt / __todoReq 변수에만 둠 (문서는 건드리지 않음)
BODY_JS = r"""(function(){
 var T = window, now = new Date().getTime();
 if (typeof T.__todoTxt === 'string') { var s = T.__todoTxt; T.__todoTxt = null; T.__todoReq = 0; return 'TEXT:' + s; }
 if (T.__todoReq && now - T.__todoReq < 15000) return 'WAIT';
 var names = ['HwpCtrl', 'hwpCtrl', 'HwpCtrlImpl', 'webHwpCtrl', 'WebHwpCtrl'];
 function find(w, depth) {
  var i, o;
  try { for (i = 0; i < names.length; i++) { o = w[names[i]]; if (o && typeof o.GetTextFile !== 'undefined') return o; } } catch (e) { return null; }
  if (depth > 4) return null;
  try { for (i = 0; i < w.frames.length; i++) { o = find(w.frames[i], depth + 1); if (o) return o; } } catch (e) {}
  return null;
 }
 var c = find(T, 0);
 if (!c) return 'NOCTRL';
 T.__todoReq = now;
 try {
  var r = c.GetTextFile('TEXT', '', function (d) { T.__todoTxt = (d == null) ? '' : String(d); });
  if (typeof r === 'string' && r.length) { T.__todoReq = 0; return 'TEXT:' + r; }
 } catch (e) { T.__todoReq = 0; return 'ERR:' + (e.message || e); }
 return 'REQ';
})()"""


# 결재시행 목록 표 읽기 식 (ES3). 이 화면과 작은 화면(깊이 4까지)의 <table>에서 칸 글자가 정확히 '처리구분'이고
# '제목'(또는 '문서제목')인 머리글 행을 찾아, 그 아래 행(머리글과 데이터가 다른 표로 나뉜 화면이면 다음 표의 행)을
# 칸 구분 \x1f, 행 구분 \x1e로 이어 돌려줌. 칸에 title 속성(말줄임 전 전체 제목)이 더 길면 그것을 씀. 화면은 읽기만 함.
LIST_JS = r"""(function(){
 var US = String.fromCharCode(31), RS = String.fromCharCode(30);
 function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ''); }
 function clean(s) { return String(s == null ? '' : s).replace(/[\x1e\x1f]/g, ' ').replace(/^\s+|\s+$/g, ''); }
 function cellText(c) {
  var t = clean(c.innerText != null ? c.innerText : c.textContent), a, tt = c.title || '';
  try { a = c.getElementsByTagName('a'); if (!tt && a.length) tt = a[0].title || ''; } catch (e) {}
  tt = clean(tt);
  return tt.length > t.length ? tt : t;
 }
 function headCells(r) { var out = [], i; for (i = 0; i < r.cells.length; i++) out.push(clean(r.cells[i].innerText != null ? r.cells[i].innerText : r.cells[i].textContent)); return out; }
 function rowCells(r) { var out = [], i; for (i = 0; i < r.cells.length; i++) out.push(cellText(r.cells[i])); return out; }
 function isHeader(r) {
  var i, n, role = false, title = false;
  for (i = 0; i < r.cells.length; i++) { n = norm(r.cells[i].innerText != null ? r.cells[i].innerText : r.cells[i].textContent);
   if (n == '처리구분') role = true; if (n == '제목' || n == '문서제목') title = true; }
  return role && title;
 }
 function fromDoc(d) {
  var tables, i, j, k, rows, out, head, t2;
  try { tables = d.getElementsByTagName('table'); } catch (e) { return null; }
  for (i = 0; i < tables.length; i++) {
   rows = tables[i].rows;
   for (j = 0; j < rows.length; j++) {
    if (!isHeader(rows[j])) continue;
    head = headCells(rows[j]); out = [head.join(US)];
    for (k = j + 1; k < rows.length; k++) out.push(rowCells(rows[k]).join(US));
    if (out.length == 1 && i + 1 < tables.length) {
     t2 = tables[i + 1].rows;
     for (k = 0; k < t2.length; k++) if (t2[k].cells.length == head.length) out.push(rowCells(t2[k]).join(US));
    }
    return 'LIST:' + out.join(RS);
   }
  }
  return null;
 }
 function walk(w, depth) {
  var r = null, i;
  try { r = fromDoc(w.document); } catch (e) {}
  if (r || depth > 4) return r;
  try { for (i = 0; i < w.frames.length; i++) { r = walk(w.frames[i], depth + 1); if (r) return r; } } catch (e) {}
  return null;
 }
 return walk(window, 0) || 'NOLIST';
})()"""


# 내부망 점검 5차용 표 모양 식 (ES3): 화면·작은 화면마다 <table>의 위치(작은 화면 이름 경로), 행 수, 처음 3행 칸 글자.
# 표 구분 \x1d, 항목 구분 \x1c, 행 구분 \x1e, 칸 구분 \x1f. 글자 가리기는 Python(format_diag)에서 함.
DIAG_JS = r"""(function(){
 var US = String.fromCharCode(31), RS = String.fromCharCode(30), FS = String.fromCharCode(28), GS = String.fromCharCode(29);
 var out = [];
 function clean(s) { return String(s == null ? '' : s).replace(/[\x1c-\x1f]/g, ' ').replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, ''); }
 function fromDoc(d, path) {
  var tables, i, j, k, rows, lines, cells;
  try { tables = d.getElementsByTagName('table'); } catch (e) { return; }
  for (i = 0; i < tables.length; i++) {
   rows = tables[i].rows; lines = [];
   for (j = 0; j < rows.length && j < 3; j++) {
    cells = [];
    for (k = 0; k < rows[j].cells.length && k < 15; k++) cells.push(clean(rows[j].cells[k].innerText != null ? rows[j].cells[k].innerText : rows[j].cells[k].textContent).substring(0, 40));
    lines.push(cells.join(US));
   }
   out.push(path + FS + rows.length + FS + lines.join(RS));
  }
 }
 function walk(w, path, depth) {
  var i, name;
  try { fromDoc(w.document, path); } catch (e) {}
  if (depth > 4) return;
  try { for (i = 0; i < w.frames.length; i++) { name = ''; try { name = w.frames[i].name || ''; } catch (e) {}
   walk(w.frames[i], path + '/' + (name || i), depth + 1); } } catch (e) {}
 }
 walk(window, 'main', 0);
 return out.join(GS);
})()"""

_MASK_RE = re.compile(r"[가-힣]|[0-9]|[A-Za-z]")
# 점검 결과에 그대로 남겨도 되는 머리글 낱말 (그 밖의 글자는 사람 이름·부서·문서번호일 수 있어 모양만)
_HEADER_WORD_RE = re.compile(
    r"^(?:[가-힣]{0,4}(?:번호|구분|일시|일자|상태|제목|종류|부서|유형|현황|여부|기한|서식명)"
    r"|입안자|기안자|수신자|발신자|처리자|접수자|결재자|순번|선택|첨부|No\.?)$"
)


def mask(text: str) -> str:
    """글자 모양만 남김: 한글→가, 숫자→#, 영문→a (점검 결과에 실제 내용이 남지 않게)."""
    return _MASK_RE.sub(lambda m: "가" if "가" <= m.group() <= "힣" else ("#" if m.group().isdigit() else "a"), text)


def _looks_like_header(cells: List[str]) -> bool:
    """머리글 행으로 볼 첫 행: 머리글 낱말 칸이 3개 이상이고 숫자가 없음 (머리글·행 표가 나뉜 화면의 첫 데이터 행은 아님)."""
    if any(re.search(r"\d", c) for c in cells):
        return False
    return sum(1 for c in cells if _HEADER_WORD_RE.match(c.replace(" ", ""))) >= 3


def safe_headers(headers: List[str]) -> List[str]:
    """머리글 낱말만 그대로, 그 밖의 칸은 모양만."""
    return [h if _HEADER_WORD_RE.match(h.replace(" ", "")) else mask(h) for h in headers]


def format_diag(raw: Optional[str]) -> str:
    """DIAG_JS 결과 → 사람이 읽을 점검 결과. 첫 행의 머리글 낱말(번호·구분·일시 등)만 그대로, 나머지는 모양만."""
    if not raw:
        return "  표 없음"
    out = []
    for table in raw.split("\x1d"):
        parts = table.split("\x1c")
        if len(parts) < 3:
            continue
        path, count, body = parts
        out.append(f"  [{path} 행 {count}]")
        for i, line in enumerate(body.split("\x1e") if body else []):
            raw_cells = line.split("\x1f")
            cells = safe_headers(raw_cells) if i == 0 and _looks_like_header(raw_cells) else [mask(c) for c in raw_cells]
            out.append("    " + " | ".join(cells))
    return "\n".join(out) or "  표 없음"


def parse_list_js_result(raw: Optional[str]) -> Tuple[List[str], List[List[str]]]:
    """LIST_JS 결과 → (머리글, 행들). 표가 없거나 형식이 다르면 ([], [])."""
    if not raw or not raw.startswith("LIST:"):
        return [], []
    lines = raw[5:].split("\x1e")
    headers = lines[0].split("\x1f")
    return headers, [line.split("\x1f") for line in lines[1:]]


class ListWindowResult(NamedTuple):
    hwnd: int
    title: str
    rows: List[ListRow]


def parse_js_result(raw: Optional[str]) -> tuple:
    """BODY_JS가 돌려준 글자 → (상태, 본문/오류)."""
    if raw is None:
        return NOJS, ""
    if raw.startswith("TEXT:"):
        return TEXT, raw[5:]
    if raw.startswith("ERR:"):
        return ERR, raw[4:]
    if raw in (REQ, WAIT, NOCTRL):
        return raw, ""
    return NOJS, ""


if sys.platform == "win32":
    from ctypes import wintypes

    _VARIANT, _DISPPARAMS, _GUID = _ew._VARIANT, _ew._DISPPARAMS, _ew._GUID
    _oleaut32 = _ew._oleaut32
    _oleaut32.SysAllocString.argtypes = [wintypes.LPCWSTR]
    _oleaut32.SysAllocString.restype = ctypes.c_void_p
    _ew._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _ew._user32.IsWindowVisible.argtypes = [wintypes.HWND]

    _DISPATCH_METHOD = 0x1
    _DISPATCH_PROPERTYGET = 0x2
    _FDEX_NAME_CASE_SENSITIVE = 0x1
    _oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    _IID_IDispatchEx = _ew._guid("{A6EF9860-C720-11D0-9337-00A0C90DCAA9}")
    _QueryInterface = ctypes.WINFUNCTYPE(
        _ew._HRESULT, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
    )
    _GetDispID = ctypes.WINFUNCTYPE(
        _ew._HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_long)
    )

    def _dispid(obj: int, name: str) -> int:
        """이름 → DISPID. IE9 이상 문서 모드의 화면(window)은 eval·화면이 만든 변수를 GetIDsOfNames로 못 찾아
        (DISP_E_UNKNOWNNAME) IDispatchEx.GetDispID로 다시 찾음."""
        names = (ctypes.c_wchar_p * 1)(name)
        dispid = ctypes.c_long()
        hr = _ew._vtable_fn(obj, 5, _ew._GetIDsOfNames)(
            obj, ctypes.byref(_ew._IID_NULL), names, 1, _ew._LOCALE_USER_DEFAULT, ctypes.byref(dispid)
        )
        if hr >= 0:
            return dispid.value
        ex = ctypes.c_void_p()
        if _ew._vtable_fn(obj, 0, _QueryInterface)(obj, ctypes.byref(_IID_IDispatchEx), ctypes.byref(ex)) < 0 or not ex.value:
            raise OSError(f"GetIDsOfNames({name}) 실패: {hr & 0xFFFFFFFF:#x}")
        bname = _oleaut32.SysAllocString(name)
        try:
            hr = _ew._vtable_fn(ex.value, 7, _GetDispID)(ex.value, bname, _FDEX_NAME_CASE_SENSITIVE, ctypes.byref(dispid))
        finally:
            _oleaut32.SysFreeString(bname)
            _ew._release(ex.value)
        if hr < 0:
            raise OSError(f"GetDispID({name}) 실패: {hr & 0xFFFFFFFF:#x}")
        return dispid.value

    def _invoke_method(obj: int, name: str, args: List[str], flags: int = _DISPATCH_METHOD) -> Optional[str]:
        """IDispatch 늦은 바인딩으로 글자 인수 메서드를 부름(또는 속성을 읽음). 글자 결과만 돌려줌 (그 밖에는 None)."""
        dispid = ctypes.c_long(_dispid(obj, name))
        argv = (_VARIANT * max(1, len(args)))()
        for i, a in enumerate(reversed(args)):  # IDispatch 인수는 뒤에서부터
            argv[i].vt = _ew._VT_BSTR
            argv[i].ptr = _oleaut32.SysAllocString(a)
        params = _DISPPARAMS()
        params.rgvarg = ctypes.cast(argv, ctypes.c_void_p)
        params.cArgs = len(args)
        result = _VARIANT()
        try:
            hr = _ew._vtable_fn(obj, 6, _ew._Invoke)(
                obj, dispid.value, ctypes.byref(_ew._IID_NULL), _ew._LOCALE_USER_DEFAULT,
                flags, ctypes.byref(params), ctypes.byref(result), None, None,
            )
            if hr < 0:
                raise OSError(f"Invoke({name}) 실패: {hr & 0xFFFFFFFF:#x}")
            return ctypes.wstring_at(result.ptr) if result.vt == _ew._VT_BSTR and result.ptr else None
        finally:
            _oleaut32.VariantClear(ctypes.byref(result))
            for i in range(len(args)):
                _oleaut32.VariantClear(ctypes.byref(argv[i]))

    def _string_property(obj: int, name: str) -> Optional[str]:
        return _invoke_method(obj, name, [], flags=_DISPATCH_PROPERTYGET)

    def _run_js(window: int, code: str) -> Optional[str]:
        """화면에서 식을 실행하고 글자 결과를 받음. IE9 이상 문서 모드는 eval, 옛 모드는 execScript."""
        try:
            return _invoke_method(window, "eval", [code])
        except OSError:
            pass
        try:
            _invoke_method(window, "execScript", [f"window.__todoR = {code};", "JScript"])
            return _string_property(window, "__todoR")
        except OSError:
            return None

    def _window_title(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(512)
        _ew._user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value

    def _read_ie(ie_hwnd) -> Optional[str]:
        return _read_ie_js(ie_hwnd, BODY_JS)


def read_document_windows(
    window_class: str = DEFAULT_WINDOW_CLASS,
    skip: Optional[Callable[[int, str], bool]] = None,
) -> List[DocWindowResult]:
    """IE 화면이 있는 Edge 창마다 기안기 본문 읽기를 한 단계 진행하고 결과를 돌려줌.

    skip(창 번호, 창 제목)이 True인 창은 화면에서 아무것도 실행하지 않고 건너뜀
    (이미 본문을 받은 문서 창에서 꺼내기를 되풀이하지 않게). Windows가 아니면 빈 목록.
    """
    if sys.platform != "win32":
        return []
    class_re = re.compile(f"^(?:{window_class})$")
    hr = _ew._ole32.CoInitialize(None)
    try:
        results: List[DocWindowResult] = []
        for top in _ew._top_windows(class_re):
            if not _ew._user32.IsWindowVisible(top):
                continue
            ies = _ew._ie_servers(top)
            if not ies:
                continue
            title = _window_title(top)
            if skip and skip(top, title):
                continue
            best = (NOJS, "")
            for ie in ies:
                status, text = parse_js_result(_read_ie(ie))
                if status != NOJS:
                    best = (status, text)
                if status in (TEXT, REQ, WAIT, ERR):
                    break  # 기안기가 있는 화면을 찾음
            results.append(DocWindowResult(top, title, *best))
        return results
    finally:
        if hr >= 0:
            _ew._ole32.CoUninitialize()


def _read_ie_js(ie_hwnd, code: str) -> Optional[str]:
    """IE 화면 하나에서 식을 실행해 글자 결과를 받음 (Windows 전용)."""
    doc = _ew._html_document(ie_hwnd)
    if not doc:
        return None
    win = None
    try:
        v = _ew._get_property(doc, "parentWindow")
        if v.vt != _ew._VT_DISPATCH or not v.ptr:
            _oleaut32.VariantClear(ctypes.byref(v))
            return None
        win = v.ptr
        return _run_js(win, code)
    except OSError:
        return None
    finally:
        _ew._release(win)
        _ew._release(doc)


def read_list_windows(window_class: str = DEFAULT_WINDOW_CLASS) -> List[ListWindowResult]:
    """IE 화면이 있는 Edge 창마다 결재시행 목록 표를 찾아 읽음 (화면은 읽기만 함). 표가 있는 창만 돌려줌.

    Windows가 아니면 빈 목록.
    """
    if sys.platform != "win32":
        return []
    class_re = re.compile(f"^(?:{window_class})$")
    hr = _ew._ole32.CoInitialize(None)
    try:
        results: List[ListWindowResult] = []
        for top in _ew._top_windows(class_re):
            if not _ew._user32.IsWindowVisible(top):
                continue
            for ie in _ew._ie_servers(top):
                headers, rows = parse_list_js_result(_read_ie_js(ie, LIST_JS))
                if headers:
                    results.append(ListWindowResult(top, _window_title(top), parse_list_table(headers, rows)))
                    break
        return results
    finally:
        if hr >= 0:
            _ew._ole32.CoUninitialize()


def check_list_windows(window_class: str = DEFAULT_WINDOW_CLASS) -> str:
    """내부망 점검 5차: Edge 창의 IE 화면마다 표 모양과, 결재시행 목록으로 읽힌 행 수를 글자 모양만 남겨 보여줌."""
    if sys.platform != "win32":
        return "Windows에서만 확인할 수 있습니다."
    class_re = re.compile(f"^(?:{window_class})$")
    hr = _ew._ole32.CoInitialize(None)
    try:
        out = []
        for top in _ew._top_windows(class_re):
            if not _ew._user32.IsWindowVisible(top):
                continue
            ies = _ew._ie_servers(top)
            if not ies:
                continue
            out.append(f"창: {mask(_window_title(top))}  (IE 화면 {len(ies)}개)")
            for n, ie in enumerate(ies):
                out.append(f" IE 화면 {n + 1}")
                out.append(format_diag(_read_ie_js(ie, DIAG_JS)))
                headers, rows = parse_list_js_result(_read_ie_js(ie, LIST_JS))
                parsed = parse_list_table(headers, rows) if headers else []
                kinds = [classify_row(r) for r in parsed]
                out.append(
                    f"  → 결재시행 목록 표: {'찾음' if headers else '못 찾음'}"
                    + (f", 머리글 {safe_headers(headers)}, 행 {len(parsed)} (업무 {kinds.count('task')}, 확인 필요 {kinds.count('review')})" if headers else "")
                )
        return "\n".join(out) or "IE 화면이 있는 Edge 창이 없습니다. 업무관리시스템 결재시행 목록을 열고 다시 실행하세요."
    finally:
        if hr >= 0:
            _ew._ole32.CoUninitialize()


if __name__ == "__main__":
    if "--check-list" in sys.argv:
        cls = sys.argv[sys.argv.index("--check-list") + 1] if len(sys.argv) > sys.argv.index("--check-list") + 1 else DEFAULT_WINDOW_CLASS
        print(check_list_windows(cls))
    else:
        print("사용법: python -m collectors.workmgmt_window --check-list [창 종류]")
