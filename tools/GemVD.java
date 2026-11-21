/*
 * GemVD.java — виртуальный дисплей через app_process (shell/adb права, без root).
 *
 * Сборка:
 *   javac -source 11 -target 11 -cp android.jar -d build GemVD.java
 *   d8 --lib android.jar --min-api 26 --output build build/GemVD.class
 *   (cd build && zip gemvd.jar classes.dex)
 *
 * Запуск:
 *   adb push build/gemvd.jar /data/local/tmp/gemvd.jar
 *   adb shell CLASSPATH=/data/local/tmp/gemvd.jar app_process /system/bin GemVD create 1080 2344 420 com.termux/gemphone-vd com.termux
 *   adb shell CLASSPATH=/data/local/tmp/gemvd.jar app_process /system/bin GemVD list
 *
 * Вывод: DISPLAY_ID=<n> / STATUS=ok  (процесс держит дисплей до убийства).
 */
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.os.Looper;
import android.view.Surface;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

public class GemVD {
    private static VirtualDisplay vd;

    public static void main(String[] args) {
        String cmd = args.length > 0 ? args[0] : "list";
        try {
            Looper.prepareMainLooper();

            Class<?> at = Class.forName("android.app.ActivityThread");
            Method systemMain = at.getDeclaredMethod("systemMain");
            systemMain.setAccessible(true);
            Object app = systemMain.invoke(null);

            Class<?> ctxImpl = Class.forName("android.app.ContextImpl");
            Method createSystem = ctxImpl.getDeclaredMethod("createSystemContext", at);
            createSystem.setAccessible(true);
            Object ctx = createSystem.invoke(null, app);

            // shell-процессу нельзя создавать дисплей «от себя»:
            // packageName контекста должен совпадать с уже установленным пакетом.
            // пакет должен быть от uid, который запускает процесс.
            // Варианты: shell-процесс без пакета, либо реальный uid приложения.
            String pkg = args.length > 5 ? args[5] : "com.android.shell";
            if (pkg == null) {
                try {
                    Method getUid = ctxImpl.getMethod("getPackageName");
                    pkg = (String) getUid.invoke(ctx);
                } catch (Throwable ignore) {
                    pkg = "android";
                }
            }
            Field f = null;
            Class<?> c = ctx.getClass();
            while (c != null && f == null) {
                try {
                    f = c.getDeclaredField("mPackageName");
                } catch (NoSuchFieldException e) {
                    c = c.getSuperclass();
                }
            }
            if (f != null) {
                f.setAccessible(true);
                f.set(ctx, pkg);
            }

            Method getSystemService =
                    Class.forName("android.content.Context").getMethod("getSystemService", Class.class);
            DisplayManager dm = (DisplayManager) getSystemService.invoke(ctx, DisplayManager.class);
            if (dm == null) {
                System.out.println("ERROR=no DisplayManager");
                return;
            }

            if ("create".equals(cmd)) {
                int w = args.length > 1 ? Integer.parseInt(args[1]) : 1080;
                int h = args.length > 2 ? Integer.parseInt(args[2]) : 2344;
                int dpi = args.length > 3 ? Integer.parseInt(args[3]) : 420;
                String name = args.length > 4 ? args[4] : pkg + "/gemphone-vd";

                int flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC
                        | DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY;

                Method create = DisplayManager.class.getMethod(
                        "createVirtualDisplay",
                        String.class, int.class, int.class, int.class,
                        Surface.class, int.class);
                vd = (VirtualDisplay) create.invoke(dm, name, w, h, dpi, null, flags);
                if (vd == null) {
                    System.out.println("ERROR=createVirtualDisplay returned null");
                    return;
                }
                System.out.println("DISPLAY_ID=" + vd.getDisplay().getDisplayId());
                System.out.println("STATUS=ok");
                System.out.flush();

                // Дисплей живёт, пока жив процесс: держим до 12 часов.
                Thread.sleep(12L * 60L * 60L * 1000L);
            } else if ("list".equals(cmd)) {
                for (android.view.Display d : dm.getDisplays()) {
                    System.out.println("DISPLAY " + d.getDisplayId() + " " + d.getName());
                }
            } else if ("flags".equals(cmd)) {
                // Перебор флагов — ищем комбинацию, разрешённую shell-процессу
                int[] combos = {
                        0,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC | DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY,
                        0x20,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_PRESENTATION,
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_SECURE,
                };
                Method create = DisplayManager.class.getMethod(
                        "createVirtualDisplay",
                        String.class, int.class, int.class, int.class,
                        Surface.class, int.class);
                for (int fl : combos) {
                    try {
                        VirtualDisplay d = (VirtualDisplay) create.invoke(
                                dm, pkg + "/vd-" + fl, 540, 1170, 240, null, fl);
                        System.out.println("OK flags=" + fl + " id=" + d.getDisplay().getDisplayId());
                    } catch (Throwable e) {
                        Throwable r = e;
                        while (r.getCause() != null) r = r.getCause();
                        String msg = r.getMessage();
                        if (msg != null && msg.length() > 200) msg = msg.substring(0, 200);
                        System.out.println("FAIL flags=" + fl + " -> " + r.getClass().getSimpleName() + ": " + msg);
                    }
                }
            } else {
                System.out.println("usage: GemVD [create W H DPI NAME PACKAGE | list | flags]");
            }
        } catch (Throwable t) {
            Throwable root = t;
            while (root.getCause() != null) root = root.getCause();
            System.out.println("ERROR=" + root.getClass().getSimpleName() + ": " + root.getMessage());
            root.printStackTrace(System.out);
        }
    }
}
