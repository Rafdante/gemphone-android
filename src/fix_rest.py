import pathlib
q = pathlib.Path('bridge/app/src/main/java/com/rafdante/gemphone/bridge/GemAccessibilityService.kt')
t = q.read_text()

t = t.replace('''"POWER", "KEYCODE_POWER" -> GLOBAL_ACTION_POWER_DIALOG''',
              '''"POWER", "POWER_DIALOG", "KEYCODE_POWER" -> GLOBAL_ACTION_POWER_DIALOG''')

old = '''            "open_url" -> {
                val url = body.optString("url")
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("url", url)
            }'''
new = '''            "open_url" -> {
                val url = body.optString("url").trim()
                val scheme = Uri.parse(url).scheme?.lowercase()
                if (scheme != "http" && scheme != "https") {
                    return JSONObject().put("ok", false).put("error", "Only http(s) URLs are allowed")
                }
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("url", url)
            }'''
assert old in t
t = t.replace(old, new)
q.write_text(t)
print("service/app patched")

p = pathlib.Path('bridge/app/src/main/java/com/rafdante/gemphone/bridge/LocalHttpServer.kt')
s = p.read_text()
old2 = '''                } catch (_: Exception) {'''
print("catch patterns:", s.count("catch (_: Exception)"))
p.write_text(s)
