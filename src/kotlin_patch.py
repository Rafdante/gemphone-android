import re, sys, pathlib
p = pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else pathlib.Path.home()/'gemphone-android/bridge/app/src/main/java/com/rafdante/gemphone/bridge/GemAccessibilityService.kt'
s = p.read_text()

start = s.index('val root = rootInActiveWindow ?: return false')
end = s.index('private fun findFirstEditable')
end2 = s.index('\n    }\n', s.index('return null', end)) + len('\n    }\n')

old_block = s[start:end2]
print("=== OLD BLOCK PREVIEW ===")
print(old_block)

new_block = '''val pairs = mutableListOf<Pair<AccessibilityNodeInfo, Boolean>>()
        for (win in windows) {
            val root = win.root ?: continue
            val focused = win.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            collectEditables(root, pairs, rootInActiveWindow?.windowId)
            if (focused != null && focused.isEditable) pairs.add(0, focused to (focused.windowId == rootInActiveWindow?.windowId))
        }
        if (pairs.isEmpty()) return TypeResult(false, listOf("окно без editable-поля (rootInActiveWindow=${rootInActiveWindow != null})"), "no editable field found")
        if (rootInActiveWindow == null) return TypeResult(false, listOf("rootInActiveWindow = null"), "active window unavailable")

        val errors = mutableListOf<String>()
        for ((node, inActive) in pairs) {
            if (!supportsSetText(node)) { errors.add("field has no ACTION_SET_TEXT action"); continue }
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                errors.add("ACTION_SET_TEXT rejected")
                continue
            }
            if (pressEnter) {
                val enterOk = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
                    node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                if (!enterOk) errors.add("enter not delivered")
            }
            if (inActive) currentAppCache = null
            return TypeResult(true, errors, null)
        }
        return TypeResult(false, errors, "ACTION_SET_TEXT unsupported (custom/WebView field?)")
    }

    private fun supportsSetText(node: AccessibilityNodeInfo): Boolean {
        val actions = node.actionList ?: return node.isEditable
        return actions.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }
    }

    private fun collectEditables(
        node: AccessibilityNodeInfo?,
        out: MutableList<Pair<AccessibilityNodeInfo, Boolean>>,
        activeWindowId: Int?,
    ) {
        if (node == null) return
        if (node.isEditable) out.add(node to (activeWindowId != null && node.windowId == activeWindowId))
        for (i in 0 until node.childCount) collectEditables(node.getChild(i), out, activeWindowId)
    }

    data class TypeResult(val ok: Boolean, val warnings: List<String>, val reason: String?)
'''

s = s[:start] + new_block + s[end2:]
p.write_text(s)
print("=== PATCHED, new length:", len(s))
