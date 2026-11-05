"""EzQ 메신저 '받은쪽지' 창의 본문 읽기 (Windows 전용, 읽기 전용).

받은쪽지 창(창 종류 이름 TfrmReceiveMessage)의 본문은 창 안의 내장 IE 화면(Internet Explorer_Server)에
표시됩니다. 화면 낭독기 등이 쓰는 Windows 접근성 표준 메시지(WM_HTML_GETOBJECT)로 그 화면의 문서를 받아
본문 글자(body.innerText)만 읽습니다. 메신저 프로그램·파일을 고치지 않고 메모리 후킹도 하지 않습니다.

보낸 사람·보낸 시각 칸은 메신저가 화면에 직접 그려서 읽을 수 없습니다 (내부망 PC 점검 결과, 2026-10-30).
Python 표준 라이브러리(ctypes)만 사용합니다.
"""

import ctypes
import re
import sys
from typing import List, Tuple

DEFAULT_WINDOW_CLASS = "TfrmReceiveMessage"

# 본문 밖의 빈 틀 문서(쪽지 창의 두 번째 IE 화면은 '%s'만 들어 있음)
_EMPTY_DOC_TEXTS = {"", "%s"}

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _ole32 = ctypes.WinDLL("ole32")
    _oleacc = ctypes.WinDLL("oleacc")
    _oleaut32 = ctypes.WinDLL("oleaut32")

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    _user32.SendMessageTimeoutW.restype = ctypes.c_size_t

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _VARIANT(ctypes.Structure):
        # 64비트에서 VARIANT는 24바이트: vt(2) + 예약(6) + 값(포인터 2개 크기)
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("r1", ctypes.c_ushort),
            ("r2", ctypes.c_ushort),
            ("r3", ctypes.c_ushort),
            ("ptr", ctypes.c_void_p),
            ("pad", ctypes.c_void_p),
        ]

    class _DISPPARAMS(ctypes.Structure):
        _fields_ = [
            ("rgvarg", ctypes.c_void_p),
            ("rgdispidNamedArgs", ctypes.c_void_p),
            ("cArgs", ctypes.c_uint),
            ("cNamedArgs", ctypes.c_uint),
        ]

    _oleacc.ObjectFromLresult.argtypes = [
        ctypes.c_size_t, ctypes.POINTER(_GUID), wintypes.WPARAM, ctypes.POINTER(ctypes.c_void_p),
    ]
    _oleaut32.VariantClear.argtypes = [ctypes.POINTER(_VARIANT)]
    _ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    _ole32.IIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]

    _SMTO_ABORTIFHUNG = 0x0002
    _DISPATCH_PROPERTYGET = 0x2
    _VT_BSTR = 8
    _VT_DISPATCH = 9
    _LOCALE_USER_DEFAULT = 0x0400

    _HRESULT = ctypes.c_long
    _GetIDsOfNames = ctypes.WINFUNCTYPE(
        _HRESULT, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.c_uint, ctypes.c_uint32, ctypes.POINTER(ctypes.c_long),
    )
    _Invoke = ctypes.WINFUNCTYPE(
        _HRESULT, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(_GUID), ctypes.c_uint32,
        ctypes.c_ushort, ctypes.POINTER(_DISPPARAMS), ctypes.POINTER(_VARIANT),
        ctypes.c_void_p, ctypes.c_void_p,
    )
    _Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

    def _guid(text: str) -> "_GUID":
        g = _GUID()
        _ole32.IIDFromString(text, ctypes.byref(g))
        return g

    _IID_NULL = _GUID()
    _IID_IHTMLDocument2 = _guid("{332C4425-26CB-11D0-B483-00C04FD90119}")

    def _vtable_fn(obj: int, index: int, prototype):
        vtbl = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return prototype(vtbl[index])

    def _release(obj: int) -> None:
        if obj:
            _vtable_fn(obj, 2, _Release)(obj)

    def _get_property(obj: int, name: str) -> "_VARIANT":
        """IDispatch 늦은 바인딩으로 속성 하나를 읽음 (IDispatch: 5=GetIDsOfNames, 6=Invoke)."""
        names = (ctypes.c_wchar_p * 1)(name)
        dispid = ctypes.c_long()
        hr = _vtable_fn(obj, 5, _GetIDsOfNames)(
            obj, ctypes.byref(_IID_NULL), names, 1, _LOCALE_USER_DEFAULT, ctypes.byref(dispid)
        )
        if hr < 0:
            raise OSError(f"GetIDsOfNames({name}) 실패: {hr & 0xFFFFFFFF:#x}")
        result = _VARIANT()
        params = _DISPPARAMS()
        hr = _vtable_fn(obj, 6, _Invoke)(
            obj, dispid.value, ctypes.byref(_IID_NULL), _LOCALE_USER_DEFAULT,
            _DISPATCH_PROPERTYGET, ctypes.byref(params), ctypes.byref(result), None, None,
        )
        if hr < 0:
            raise OSError(f"Invoke({name}) 실패: {hr & 0xFFFFFFFF:#x}")
        return result

    def _class_name(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _top_windows(class_re: "re.Pattern") -> List[int]:
        found: List[int] = []

        def cb(hwnd, _lparam):
            if class_re.search(_class_name(hwnd)):
                found.append(hwnd)
            return True

        _user32.EnumWindows(_WNDENUMPROC(cb), 0)
        return found

    def _ie_servers(top_hwnd) -> List[int]:
        found: List[int] = []

        def cb(hwnd, _lparam):
            if _class_name(hwnd) == "Internet Explorer_Server":
                found.append(hwnd)
            return True

        _user32.EnumChildWindows(top_hwnd, _WNDENUMPROC(cb), 0)
        return found

    def _html_document(ie_hwnd) -> int:
        """IE 화면 하나의 문서(IHTMLDocument2) 포인터. 받지 못하면 0. 쓴 뒤 _release 해야 함."""
        msg = _user32.RegisterWindowMessageW("WM_HTML_GETOBJECT")
        lresult = ctypes.c_size_t()
        _user32.SendMessageTimeoutW(ie_hwnd, msg, 0, 0, _SMTO_ABORTIFHUNG, 2000, ctypes.byref(lresult))
        if not lresult.value:
            return 0
        doc = ctypes.c_void_p()
        if _oleacc.ObjectFromLresult(lresult.value, ctypes.byref(_IID_IHTMLDocument2), 0, ctypes.byref(doc)) < 0:
            return 0
        return doc.value or 0

    def _document_text(ie_hwnd) -> str:
        """IE 화면 하나의 body.innerText. 읽을 수 없으면 빈 문자열."""
        doc = ctypes.c_void_p(_html_document(ie_hwnd))
        if not doc.value:
            return ""
        body = None
        try:
            v = _get_property(doc.value, "body")
            if v.vt != _VT_DISPATCH or not v.ptr:
                _oleaut32.VariantClear(ctypes.byref(v))
                return ""
            body = v.ptr  # VARIANT가 가진 참조를 넘겨받아 아래에서 직접 Release
            t = _get_property(body, "innerText")
            text = ctypes.wstring_at(t.ptr) if t.vt == _VT_BSTR and t.ptr else ""
            _oleaut32.VariantClear(ctypes.byref(t))
            return text
        except OSError:
            return ""
        finally:
            _release(body)
            _release(doc.value)


def read_message_windows(window_class: str = DEFAULT_WINDOW_CLASS) -> List[Tuple[int, str]]:
    """열려 있는 받은쪽지 창마다 (창 번호, 본문)을 돌려줌. Windows가 아니면 빈 목록.

    window_class는 창 종류 이름 정규식 (메신저 버전이 바뀌어 이름이 달라지면 config.json에서 변경).
    """
    if sys.platform != "win32":
        return []
    class_re = re.compile(f"^(?:{window_class})$")
    hr = _ole32.CoInitialize(None)  # 백그라운드 스레드에서도 COM 사용 가능하게 (이미 됐으면 S_FALSE)
    try:
        results: List[Tuple[int, str]] = []
        for top in _top_windows(class_re):
            texts = [_document_text(ie).strip() for ie in _ie_servers(top)]
            body = "\n".join(t for t in texts if t not in _EMPTY_DOC_TEXTS)
            if body:
                results.append((top, body))
        return results
    finally:
        if hr >= 0:
            _ole32.CoUninitialize()
