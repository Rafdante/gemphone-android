import pathlib
p = pathlib.Path('src/gemphone.py')
s = p.read_text()

old = '''    def type_text(self, text: str, *, press_enter: bool = False) -> dict[str, Any]:
        if self.has_bridge():
            try:
                bres = self.bridge.type_text(text, press_enter=press_enter)
                if bres.get("ok", True):
                    return {"ok": True, "via": "bridge", "method": str(bres.get("method") or "accessibility"), "chars": len(text)}
            except Exception:
                pass
'''
new = '''    def type_text(self, text: str, *, press_enter: bool = False) -> dict[str, Any]:
        bridge_error: str = ""
        bridge_warnings: list[str] = []
        if self.has_bridge():
            try:
                bres = self.bridge.type_text(text, press_enter=press_enter)
                bridge_warnings = [str(w) for w in (bres.get("warnings") or [])]
                if bres.get("ok", False):
                    res = {"ok": True, "via": "bridge", "method": "accessibility", "chars": len(text)}
                    if bridge_warnings:
                        res["warnings"] = bridge_warnings
                    return res
                bridge_error = str(bres.get("reason") or "ACTION_SET_TEXT отклонён")
            except Exception as exc:
                bridge_error = f"bridge error: {exc}"

        if bridge_error and not self.adb_available():
            raise RuntimeError(
                f"Ввод текста не удался. Bridge: {bridge_error}. "
                "ADB не подключён (нужен для фолбэка) — включите беспроводную отладку и /adb. "
                "Если поле кастомное (WebView/Flutter), используйте adb_tap по полю + вставку из буфера."
            )
'''
assert old in s, "old block not found"
s = s.replace(old, new, 1)
p.write_text(s)
print("client patched ok")
