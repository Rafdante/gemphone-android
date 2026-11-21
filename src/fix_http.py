import pathlib
p = pathlib.Path('bridge/app/src/main/java/com/rafdante/gemphone/bridge/LocalHttpServer.kt')
s = p.read_text()
old = '''                    method == "POST" && path == "/type" -> {
                        val ok = service.performTypeText(body.optString("text"), body.optBoolean("press_enter", false))
                        writeJson(output, 200, JSONObject().put("ok", ok))
                    }'''
new = '''                    method == "POST" && path == "/type" -> {
                        val res = service.performTypeText(body.optString("text"), body.optBoolean("press_enter", false))
                        val out = JSONObject().put("ok", res.ok)
                        if (res.warnings.isNotEmpty()) out.put("warnings", org.json.JSONArray(res.warnings))
                        if (res.reason != null) out.put("reason", res.reason)
                        writeJson(output, 200, out)
                    }'''
assert old in s
s = s.replace(old, new)
p.write_text(s)
print("http patched")

# service: remove cache line + return type
q = pathlib.Path('bridge/app/src/main/java/com/rafdante/gemphone/bridge/GemAccessibilityService.kt')
t = q.read_text()
t = t.replace("            if (inActive) currentAppCache = null\n", "")
t = t.replace("fun performTypeText(text: String, pressEnter: Boolean): Boolean {", "fun performTypeText(text: String, pressEnter: Boolean): TypeResult {")
t = t.replace("for ((node, inActive) in pairs) {", "for ((node, _inActive) in pairs) {")
q.write_text(t)
print("service patched; TypeResult refs:", t.count("TypeResult"))
