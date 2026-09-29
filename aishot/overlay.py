"""Полупрозрачный серый оверлей выделения для Wayland (niri) — «v2».

Проблема обычного slurp: он заливает ВСЮ область (включая будущую рамку)
полупрозрачным цветом, поэтому под выделением ничего не видно.

Здесь свой оверлей на wlr-layer-shell поверх снимка экрана:
  1. grim делает снимок всего экрана;
  2. Pillow рисует кадр: всё затемнено серым до `dim` процентов яркости,
     КРОМЕ выделяемой прямоугольной области — там оригинал остаётся ярким,
     плюс серая рамка и размеры;
  3. окно-оверлей (anchored на все края, exclusive_zone=-1, keyboard
     exclusive) перерисовывается при движении мыши;
  4. drag = выбор, Enter/отпускание = подтвердить, Esc/правая кнопка = отмена.

Зависимости: pywayland + Pillow (pip install aishot[overlay] или системные
пакеты). Если чего-то нет — бросается OverlayUnavailable, и пайплайн честно
откатывается на обычный slurp (v1).
"""
from __future__ import annotations

import errno
import fcntl
import os
import select
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from .logger import LOG


class OverlayUnavailable(Exception):
    """Нет grim / Pillow / pywayland / layer-shell — оверлей недоступен."""


def _hex_to_rgb(h: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) >= 6:
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            pass
    return default


def _alpha_of(h: str, default: float) -> float:
    h = h.strip().lstrip("#")
    if len(h) == 8:
        try:
            return int(h[6:8], 16) / 255.0
        except ValueError:
            pass
    return default


# ════════════════════════ wayland low-level helpers ════════════════════════
def _load_layer_shell():
    try:
        from pywayland import ffi, lib  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise OverlayUnavailable(f"pywayland не установлен ({e})") from e

    xml_path = None
    for cand in (
        "/usr/share/wayland-protocols/staging/wlr-layer-shell-unstable-v1.xml",
        "/usr/local/share/wayland-protocols/staging/wlr-layer-shell-unstable-v1.xml",
    ):
        if os.path.exists(cand):
            xml_path = cand
            break
    if xml_path is None:
        try:
            out = subprocess.run(
                ["pkg-config", "--variable=pkgdatadir", "wayland-protocols"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            cand = os.path.join(out, "staging", "wlr-layer-shell-unstable-v1.xml")
            if os.path.exists(cand):
                xml_path = cand
        except Exception:  # noqa: BLE001
            pass
    if xml_path is None:
        raise OverlayUnavailable("Не найден XML протокола wlr-layer-shell")

    ns: dict = {}
    try:
        from pywayland.scanner import Protocol  # type: ignore
        proto = Protocol(xml_path)
        proto.provide(namespace="zwlr_layer_shell_v1_", target=ns)
    except Exception:
        from xml.etree import ElementTree
        root = ElementTree.parse(xml_path).getroot()
        iface_name = "zwlr_layer_shell_v1"
        iface = next((e for e in root.iter("interface") if e.get("name") == iface_name), None)
        if iface is None:
            raise OverlayUnavailable("В XML нет zwlr_layer_shell_v1")
        reqs = [e.get("name") for e in iface.iter("request")]
        evts = [e.get("name") for e in iface.iter("event") or []]
        ls_iface = _make_interface(iface_name, reqs, evts)
        shell_type = ls_iface
        # enum-ы берём из спецификации (стабильны)
        ns["layer_shell"] = shell_type
        ns["_enum_anchor"] = {"top": 1, "bottom": 2, "left": 4, "right": 8}
        ns["_enum_kbd"] = {"none": 0, "exclusive": 1, "on_demand": 2}
        ns["_enum_layer"] = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
        return ns

    # путь pywayland.scanner: находим интерфейс в namespace
    cls = ns.get("ZwlrLayerShellV1") or ns.get("zwlr_layer_shell_v1")
    if cls is None:
        for v in ns.values():
            if getattr(v, "name", "") == "zwlr_layer_shell_v1":
                cls = v
                break
    if cls is None:
        raise OverlayUnavailable("Сканер не вернул интерфейс слоя")
    ns["layer_shell"] = cls
    return ns


class _DynIface:
    """Минимальный interface без pywayland-зависимостей (для fallback-ветки)."""
    def __init__(self, name, requests, events):
        self.name = name
        self.requests = requests
        self.events = events


def _make_interface(name, reqs, evts):
    class I:
        pass
    I.name = name
    I.requests = reqs
    I.events = evts
    return I


# ═══════════════════════════════ оверлей ═══════════════════════════════════
class SelectionOverlay:
    def __init__(self, cfg: dict):
        s = cfg["slurp"]
        self.border_rgb = _hex_to_rgb(str(s.get("border_color", "9e9e9e")), (158, 158, 158))
        mask = str(s.get("selection_color", "3a3a3a80"))
        self.mask_rgb = _hex_to_rgb(mask, (58, 58, 58))
        # dim = сила затемнения фона. Можно задать явно отдельной опцией
        # overlay_dim (0..1), иначе берётся из альфы selection_color.
        # dim=0 или альфа 00 — фон НЕ затемняется вообще (чистый экран).
        dim_cfg = s.get("overlay_dim", None)
        if dim_cfg is not None:
            try:
                self.dim = max(0.0, min(1.0, float(dim_cfg)))
            except (TypeError, ValueError):
                LOG.warning("Некорректный overlay_dim '%s', беру альфу из selection_color", dim_cfg)
                self.dim = max(0.0, min(1.0, _alpha_of(mask, 0.5)))
        else:
            self.dim = max(0.0, min(1.0, _alpha_of(mask, 0.5)))
        self.border_w = max(1, int(s.get("border_weight", 2)))
        self.show_dims = bool(s.get("show_dimensions", True))

        self._ids = iter(range(1 << 20, 1 << 21))
        self.objs: dict[int, object] = {}
        self.globals: dict[str, tuple[int, int]] = {}
        self.sel: tuple[int, int, int, int] | None = None   # x0,y0,x1,y1 logical
        self.mouse: tuple[int, int] | None = None
        self.dragging = False
        self.p0: tuple[int, int] | None = None
        self.result: tuple[int, int, int, int] | None = None
        self.cancelled = False
        self.done = False
        self.frame_pending = False
        self.buffer_ok = False
        self.surface_configured = False
        self._base = None          # PIL Image (physical px)
        self._png_cache: bytes | None = None

    # ---------- connection ----------
    def connect(self, scale: int) -> None:
        try:
            from pywayland.client import Display
            from pywayland.protocol.wayland import (
                WlCompositor, WlKeyboard, WlPointer, WlSeat, WlShm, WlSurface,
            )
        except Exception as e:  # noqa: BLE001
            raise OverlayUnavailable(f"pywayland недоступен ({e})") from e
        try:
            from PIL import Image  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise OverlayUnavailable(f"Pillow недоступен ({e})") from e

        self._Wl = dict(compositor=WlCompositor, seat=WlSeat, pointer=WlPointer,
                        keyboard=WlKeyboard, shm=WlShm, surface=WlSurface)

        try:
            disp = Display()
            disp.connect()
        except Exception as e:  # noqa: BLE001
            raise OverlayUnavailable(f"Wayland недоступен ({e})") from e
        self.display = disp
        self.fd = disp.get_fd()
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        reg = disp.get_registry()
        reg.dispatcher["global"] = self._on_global
        disp.roundtrip()

        need = ["wl_compositor", "wl_shm", "wl_seat", "zwlr_layer_shell_v1"]
        for n in need:
            if n not in self.globals:
                raise OverlayUnavailable(f"Композитор не поддерживает {n}")

        ns = _load_layer_shell()
        self.shell_iface = ns["layer_shell"]

        self.compositor = self._bind("wl_compositor", WlCompositor, 4)
        self.shm = self._bind("wl_shm", WlShm, 1)
        self.seat = self._bind("wl_seat", WlSeat, 7)
        self.shell = self._bind("zwlr_layer_shell_v1", self.shell_iface, 3)

        # surface
        sid = next(self._ids)
        surf = WlSurface.proxy_class(None, self.display)
        surf._display = self.display
        surf._ptr = None
        surf.user_data = None
        from pywayland import lib as wl, ffi as wffi
        surf.dispatcher = type(surf.dispatcher)(surf.interface.events)
        c_ptr = wl.wl_proxy_marshal_constructor(self.compositor._ptr, 0, surf.interface._ptr, None, wffi.NULL, sid)
        surf._ptr = c_ptr
        self.objs[sid] = surf
        self.surface = surf

        # layer surface
        lid = next(self._ids)
        ls_cls = self.shell_iface
        ls = ls_cls.proxy_class.__new__(ls_cls.proxy_class) if hasattr(ls_cls, "proxy_class") else None
        # строим прокси вручную
        ls_obj = _LayerProxy(ls_cls, self.display)
        ls_id = next(self._ids)
        ptr = wl.wl_proxy_marshal_constructor_versioned(
            self.shell._ptr, 0, ls_cls._ptr, None, wffi.NULL, ls_id,
            surf._ptr, 0, wffi.NULL, 3)
        ls_obj._ptr = ptr
        self.objs[ls_id] = ls_obj
        self.layer = ls_obj

        # configure event on layer
        ls_obj.dispatcher["configure"] = self._on_configure
        # size + anchors + exclusive zone + keyboard
        ls_obj.set_size(self.logical_w, self.logical_h)
        ls_obj.set_anchor(1 | 2 | 4 | 8)
        ls_obj.set_exclusive_zone(-1)
        ls_obj.set_keyboard_interactivity(1)  # exclusive
        self.display.flush()

        # pointer & keyboard
        pid = next(self._ids)
        self.pointer = self.seat._marshal_constructor(0, WlPointer, pid)
        self.pointer.dispatcher["motion"] = self._on_motion
        self.pointer.dispatcher["button"] = self._on_button
        kid = next(self._ids)
        self.keyboard = self.seat._marshal_constructor(1, WlKeyboard, kid)
        self.keyboard.dispatcher["key"] = self._on_key

        # физический размер экрана из снимка
        self.scale = scale
        self._grab_screen()
        self.w, self.h = self._base.size
        self.logical_w, self.logical_h = self.w // scale, self.h // scale
        self.layer.set_size(self.logical_w, self.logical_h)

        # shm pool
        memfd = os.memfd_create(b"aishot-overlay".decode())
        stride = self.w * 4
        size = stride * self.h
        os.ftruncate(memfd, size)
        mm = mmap.mmap(memfd, size)
        self.mm = mm
        os.close(memfd)
        pool_id = next(self._ids)
        self.pool = self.shm._marshal_constructor(0, WlShmPool, pool_id, mm.fileno(), size)
        mm.close()

        self.display.roundtrip()
        LOG.info("Оверлей v2 подключён (%dx%d, scale=%d)", self.w, self.h, scale)

    def _bind(self, gname, iface, version):
        gid, _ver = self.globals[gname]
        oid = next(self._ids)
        obj = iface.proxy_class(None, self.display)
        obj._ptr = None
        ptr = _lib.wl_proxy_marshal_constructor_versioned(
            self.registry_ptr, 0, iface._ptr, None, _ffi.NULL, gid, min(_ver, version), oid)
        obj._ptr = ptr
        self.objs[oid] = obj
        return obj

    def _on_global(self, registry, gid, iface, version):
        self.globals[iface] = (gid, version)

    # ---------- screen grab ----------
    def _grab_screen(self) -> None:
        from PIL import Image
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            r = subprocess.run(["grim", tmp.name], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                raise OverlayUnavailable(f"grim не смог снять экран: {r.stderr.strip()}")
            self._base = Image.open(tmp.name).convert("RGB")
            self._base.load()
        finally:
            os.unlink(tmp.name)

    # ---------- drawing ----------
    def render_frame(self) -> bytes:
        from PIL import Image, ImageDraw
        base = self._base
        frame = base.copy()
        if self.dim > 0.0:
            dark = Image.new("RGB", base.size, self.mask_rgb)
            frame = Image.blend(frame, dark, self.dim)
        d = ImageDraw.Draw(frame)
        if self.sel:
            x0, y0, x1, y1 = self.norm(self.sel)
            px0, py0, px1, py1 = x0 * self.scale, y0 * self.scale, x1 * self.scale, y1 * self.scale
            frame.paste(base.crop((px0, py0, px1, py1)), (px0, py0))
            bw = self.border_w
            d.rectangle([px0 - bw, py0 - bw, px1 + bw - 1, py1 + bw - 1], outline=self.border_rgb, width=bw)
            if self.show_dims:
                label = f"{x1 - x0} × {y1 - y0}"
                tw = d.textlength(label)
                ty = max(0, py0 - 24)
                d.rectangle([px0, ty, px0 + tw + 10, ty + 20], fill=(30, 30, 30))
                d.text((px0 + 5, ty + 2), label, fill=(220, 220, 220))
        elif self.mouse and self.dragging and self.p0:
            pass
        out = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        frame.save(out, format="PNG")
        data = out.getvalue()
        out.close()
        return data

    @staticmethod
    def norm(sel):
        x0, y0, x1, y1 = sel
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    # ---------- buffer push ----------
    def push_and_commit(self) -> None:
        png = self.render_frame()
        buf_id = next(self._ids)
        buf = self.pool.create_buffer(buf_id, 0, self.w, self.h, self.w * 4, 1)
        buf.dispatcher["release"] = self._on_buf_release
        self.surface.attach(buf, 0, 0)
        self.surface.damage_buffer(0, 0, self.w, self.h)
        self.surface.commit()
        self._pending_buf = buf
        self.buffer_ok = True
        self.display.flush()

    def _on_buf_release(self, buf):
        try:
            buf.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ---------- events ----------
    def _on_configure(self, layer, serial, w, h):
        self.logical_w, self.logical_h = w, h
        layer.configure_ack(serial)
        self.surface_configured = True
        self.display.flush()

    def _on_motion(self, pointer, time_us, sx, sy):
        self.mouse = (int(round(sx)), int(round(sy)))
        if self.dragging and self.p0:
            self.sel = (self.p0[0], self.p0[1], self.mouse[0], self.mouse[1])
            self._schedule_redraw()

    def _on_button(self, pointer, serial, time_us, button, state):
        BTN_LEFT, BTN_RIGHT, BTN_EXTRA = 0x110, 0x111, 0x112
        if button == BTN_RIGHT and state == 1:
            self.cancelled = True
            self.done = True
            return
        if button == BTN_LEFT:
            if state == 1:  # press
                self.dragging = True
                self.p0 = self.mouse or (0, 0)
                self.sel = (self.p0[0], self.p0[1], self.p0[0], self.p0[1])
            else:           # release
                if self.dragging and self.sel:
                    x0, y0, x1, y1 = self.norm(self.sel)
                    if (x1 - x0) >= 4 and (y1 - y0) >= 4:
                        self.result = (x0, y0, x1, y1)
                        self.done = True
                    else:
                        self.dragging = False
                        self.sel = None
                        self._schedule_redraw()
                self.dragging = False

    KEY_ESC, KEY_ENTER, KEY_KPENTER = 1, 28, 96

    def _on_key(self, keyboard, serial, time_us, key, state):
        if state != 1:
            return
        if key == self.KEY_ESC:
            self.cancelled = True
            self.done = True
        elif key in (self.KEY_ENTER, self.KEY_KPENTER):
            if self.sel:
                x0, y0, x1, y1 = self.norm(self.sel)
                if (x1 - x0) >= 4 and (y1 - y0) >= 4:
                    self.result = (x0, y0, x1, y1)
                    self.done = True

    def _schedule_redraw(self) -> None:
        now = time.monotonic()
        if now - getattr(self, "_last_draw", 0.0) < 0.033:
            return
        self._last_draw = now
        if not self.frame_pending:
            self.frame_pending = True
            cb_id = next(self._ids)
            cb = self.surface.frame(cb_id)
            cb.dispatcher["done"] = self._on_frame_done
            self.surface.commit()
            self.display.flush()

    def _on_frame_done(self, callback, ts):
        self.frame_pending = False
        try:
            callback.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.push_and_commit()

    # ---------- main loop ----------
    def run(self, timeout: float = 120.0) -> tuple[int, int, int, int]:
        deadline = time.monotonic() + timeout
        self.push_and_commit()
        while not self.done:
            if time.monotonic() > deadline:
                raise TimeoutError("Выбор через оверлей прерван по таймауту")
            self.display.flush()
            try:
                select.select([self.fd], [], [], 0.05)
            except InterruptedError:
                continue
            try:
                self.display.read()
            except OSError:
                break
            self.display.dispatch(block=False)
        if self.cancelled or self.result is None:
            raise ScreenshotCancelledOvl()
        return self.result

    def destroy(self) -> None:
        try:
            self.display.disconnect()
        except Exception:  # noqa: BLE001
            try:
                self.display.destroy()
            except Exception:  # noqa: BLE001
                pass


class ScreenshotCancelledOvl(Exception):
    pass


# ── маленькие обёртки, чтобы не тянуть lib/ffi повсюду ──
try:
    from pywayland import ffi as _ffi, lib as _lib
except Exception:  # noqa: BLE001
    _ffi = _lib = None

try:
    import mmap
except Exception:  # noqa: BLE001
    mmap = None


class _LayerProxy:
    """Динамический прокси zwlr_layer_surface_v1 (requests отправляются marshal'ом)."""
    def __init__(self, iface, display):
        from pywayland import lib as wl, ffi as wffi
        self._wl, self._wffi = wl, wffi
        self.interface = iface
        self._display = display
        self._ptr = None
        self.dispatcher = _DispatcherStub(iface)
        display._children.add(self)

    # generic request sender by name
    def _req(self, name: str, *args):
        idx = self.interface.requests.index(name)
        arg_ptrs = []
        # build wl_argument array via cffi
        nargs = len(args)
        arr = self._wffi.new(f"struct wl_argument [{max(nargs,1)}]")
        for i, a in enumerate(args):
            if isinstance(a, int):
                arr[i].u = a
            elif a is None:
                arr[i].o = self._wffi.NULL
            else:  # proxy
                arr[i].o = a._ptr
        self._wl.wl_proxy_marshal_array(self._ptr, idx, arr)

    def set_size(self, w, h): self._req("set_size", w, h)
    def set_anchor(self, bits): self._req("set_anchor", bits)
    def set_exclusive_zone(self, z): self._req("set_exclusive_zone", z)
    def set_keyboard_interactivity(self, m): self._req("set_keyboard_interactivity", m)
    def set_margin(self, l, t, r, b): self._req("set_margin", l, t, r, b)
    def set_layer(self, v): self._req("set_layer", v)
    def configure_ack(self, serial): self._req("ack_configuration", serial)
    def destroy(self):
        try:
            self._req("destroy")
        except Exception:  # noqa: BLE001
            pass


class _DispatcherStub:
    def __init__(self, iface):
        from pywayland.dispatcher import Dispatcher
        msgs = [{"name": n} for n in iface.events]
        self._d = Dispatcher(msgs)

    def __getitem__(self, k): return self._d[k]
    def __setitem__(self, k, v): self._d[k] = v


def available(cfg: dict) -> bool:
    """Быстрая проверка: можно ли вообще запускать оверлей."""
    if str(cfg["slurp"].get("overlay_mode", "v2")).lower() not in ("v2", "2", "overlay"):
        return False
    import shutil
    if shutil.which("grim") is None:
        return False
    try:
        import pywayland  # noqa: F401
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def select_region_overlay(cfg: dict) -> str:
    """Возвращает геометрию 'X,Y WxH' в ФИЗИЧЕСКИХ пикселях (для grim -g)."""
    scale = int(float(cfg["grim"].get("scale", 1)))
    ov = SelectionOverlay(cfg)
    try:
        ov.connect(scale)
        result = ov.run()
    finally:
        ov.destroy()
    x0, y0, x1, y1 = result
    geometry = f"{x0},{y0} {x1 - x0}x{y1 - y0}"
    LOG.debug("Оверлей вернул геометрию: %s", geometry)
    return geometry
