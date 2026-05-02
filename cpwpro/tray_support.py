# -*- coding: utf-8 -*-
"""
CPW-Pro 系统托盘（可选 pystray + Pillow）。

—— 与其它入口的区别 ——
· 控制台端 CapsWriter：`util/ui/tray.py`（双击托盘隐藏控制台等）。
· CPW-Pro 图形壳：仅此模块；主程序入口仅为 `python -m cpwpro`、`cpw_pro_ui.py`、`launcher/*.bat`。

行为说明（Windows / pystray）：
左键单击托盘图标会触发菜单里 **首个 default=True** 的 MenuItem，
因此「打开主界面」必须为 default=True，否则关掉主窗后即无法从左键唤起。

环境变量：
· CPW_TRAY_NO_HIDE=1 → 关窗直接退出进程，不使用托盘隐藏。
· CPW_TRAY_DISABLE=1 → 不创建托盘（与缺少依赖时效果类似：× 即退出）。
· CPW_TRAY_ICON → 自定义托盘图标路径（ico/png）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


def optional_tray_deps_installed() -> bool:
    """未安装时可安全返回 False（不抛出）。"""
    try:
        import pystray  # noqa: F401,PLC0415
        import PIL.Image  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def tray_disabled_by_env() -> bool:
    v = os.environ.get("CPW_TRAY_DISABLE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def tray_hide_disabled_by_env() -> bool:
    """CPW_TRAY_NO_HIDE：关窗直接退出且不创建托盘图标。"""
    v = os.environ.get("CPW_TRAY_NO_HIDE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _load_tray_image(root_dir: Path):
    try:
        from PIL import Image
        from PIL import ImageDraw  # noqa: PLC0415
    except ImportError:
        return None

    try:
        res = Image.Resampling.LANCZOS
    except AttributeError:
        res = Image.LANCZOS

    candidates: list[Path] = []
    env = os.environ.get("CPW_TRAY_ICON", "").strip('"').strip()
    if env:
        candidates.append(Path(env))

    candidates.extend(
        [
            root_dir / "assets" / "cpw_pro_tray.png",
            root_dir / "assets" / "cpw_icon_tray.ico",
            root_dir / "assets" / "icon.ico",
        ]
    )

    for p in candidates:
        if p.is_file():
            try:
                im = Image.open(p).convert("RGBA")
                im.thumbnail((64, 64), res)
                return im
            except Exception:
                continue

    try:
        im = Image.new("RGBA", (64, 64), (37, 99, 235, 255))
        dr = ImageDraw.Draw(im)
        dr.rounded_rectangle([10, 10, 54, 54], radius=14, fill=(219, 234, 254, 255))
        return im
    except Exception:
        return None


class TrayController:
    def __init__(self, app, root_dir: Path) -> None:
        self._app = app
        self._root_dir = Path(root_dir)
        self._icon = None
        self._thread: threading.Thread | None = None
        self.active = False
        self._welcome_logged = False

    def start(self, log_fn) -> None:
        """
        创建托盘图标（幂等）。
        在「初始化 after_idle」与「即将关窗缩小到托盘」两处均可调用，避免与时间窗赛跑。
        """
        if tray_disabled_by_env() or tray_hide_disabled_by_env():
            return

        # 已成功启动过 pystray
        if self._icon is not None:
            return

        try:
            if not bool(self._app.winfo_exists()):
                return
        except Exception:
            return

        if not optional_tray_deps_installed():
            if not getattr(self, "_logged_missing_deps", False):
                log_fn("[Info] 可选：pip install pystray pillow 以启用「关闭窗口→托盘最小化」。")
                self._logged_missing_deps = True
            return

        try:
            import pystray  # noqa: PLC0415
            from PIL import Image  # noqa: F401,PLC0415
        except ImportError:
            if not getattr(self, "_logged_missing_deps", False):
                log_fn("[Info] 可选：pip install pystray pillow 以启用「关闭窗口→托盘最小化」。")
                self._logged_missing_deps = True
            return

        image = _load_tray_image(self._root_dir)
        if image is None:
            if not getattr(self, "_logged_no_image", False):
                log_fn("[Warn] 无法载入托盘图标，跳过托盘（请检查 Pillow / 资源文件）。")
                self._logged_no_image = True
            return

        tooltip = getattr(self._app, "WINDOW_TITLE", "CPW-Pro")

        menu = pystray.Menu(
            pystray.MenuItem(
                "打开主界面",
                self._on_open_main,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 CPW-Pro", self._on_quit),
        )

        tray_id = f"cpwpro_gui.{os.getpid()}"
        self._icon = pystray.Icon(tray_id, image, tooltip, menu)

        def run_icon() -> None:
            assert self._icon is not None
            self._icon.run()

        self._thread = threading.Thread(target=run_icon, daemon=True)
        self._thread.start()
        self.active = True
        if not self._welcome_logged:
            log_fn(
                "[Info] 托盘已启用：关主窗缩小到托盘；左键单击图标或右键「打开主界面」恢复；右键「退出」结束进程。"
            )
            self._welcome_logged = True

    def schedule_show_main(self, icon=None, item=None) -> None:
        """从托盘线程投递到 Tk 主线程。"""
        if getattr(self._app, "_finalizing_exit", False):
            return
        try:
            self._app.after(0, self._restore_main_safe)
        except Exception:
            pass

    def _on_open_main(self, icon=None, item=None) -> None:
        self.schedule_show_main(icon, item)

    def _on_quit(self, icon=None, item=None) -> None:
        try:
            self._app.after(0, self._app._finalize_and_exit)
        except Exception:
            pass

    def _restore_main_safe(self) -> None:
        if getattr(self._app, "_finalizing_exit", False):
            return
        try:
            self._restore_main_inner()
        except Exception as exc:
            try:
                self._app.log(f"[Warn] 无法从托盘恢复窗口：{exc}")
            except Exception:
                pass

    def _restore_main_inner(self) -> None:
        w = self._app
        try:
            if not w.winfo_exists():
                return
        except Exception:
            return

        try:
            st = str(w.state()).lower()
            if "withdraw" in st or st == "iconic":
                w.wm_state("normal")
        except Exception:
            try:
                w.wm_state("normal")
            except Exception:
                pass

        try:
            w.deiconify()
        except Exception:
            try:
                w.wm_deiconify()
            except Exception:
                pass

        try:
            w.lift()
            w.after(50, lambda: (w.lift() if w.winfo_exists() else None))
        except Exception:
            pass

        try:
            w.attributes("-topmost", True)

            def _untop():
                try:
                    w.attributes("-topmost", False)
                except Exception:
                    pass

            w.after(200, _untop)
        except Exception:
            pass

        try:
            w.update_idletasks()
            w.focus_force()
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        self.active = False
