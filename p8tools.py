"""Extra PICO-8 tooling: headless simulation, run/capture, and data-section authoring.

Everything here works on the .p8 text format directly. Key PICO-8 facts baked in:
  - numbers are 16.16 fixed point: anything above 32767 wraps negative, so
    simulation loops are nested to keep every counter small
  - `pico8 -x cart.p8` runs a cart headless; printh() goes to stdout prefixed
    "INFO: "; on a runtime/syntax error it prints the message and then hangs,
    so the process has to be killed by us
  - __gfx__ rows are 128 hex chars, __sfx__ rows are 168 chars
    (00 speed loop_start loop_end + 32 notes of 5 hex chars: pitch(2) wave vol fx)
  - __music__ rows are "flags ch0ch1ch2ch3"; a channel of 0x41+n means muted
"""
import base64
import ctypes
import io
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import shutil

_CANDIDATES = [
    r"C:\Program Files (x86)\PICO-8\pico8.exe",
    r"C:\Program Files\PICO-8\pico8.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\PICO-8\pico8.exe"),
    "/Applications/PICO-8.app/Contents/MacOS/pico8",
    os.path.expanduser("~/pico-8/pico8"),
    "/usr/local/bin/pico8",
]
PICO8_EXE = os.environ.get("PICO8_EXE") or next((p for p in _CANDIDATES if os.path.exists(p)), None) \
    or shutil.which("pico8") or _CANDIDATES[0]
TMP_DIR = Path(__file__).parent / "tmp"

PALETTE = [
    (0, 0, 0), (29, 43, 83), (126, 37, 83), (0, 135, 81),
    (171, 82, 54), (95, 87, 79), (194, 195, 199), (255, 241, 232),
    (255, 0, 77), (255, 163, 0), (255, 236, 39), (0, 228, 54),
    (41, 173, 255), (131, 118, 156), (255, 119, 168), (255, 204, 170),
]

SEMI = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5, "f#": 6,
        "gb": 6, "g": 7, "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11}

SECTION_ORDER = ["__lua__", "__gfx__", "__gff__", "__label__", "__map__", "__sfx__", "__music__"]


# ----------------------------------------------------------------------------- .p8 text sections
def load_p8(path):
    text = Path(path).read_text(encoding="utf-8")
    lines = text.replace("\r\n", "\n").split("\n")
    header, sections, cur = [], {}, None
    for ln in lines:
        if re.fullmatch(r"__[a-z]+__", ln):
            cur = ln
            sections[cur] = []
        elif cur is None:
            header.append(ln)
        else:
            sections[cur].append(ln)
    for k in sections:
        while sections[k] and sections[k][-1] == "":
            sections[k].pop()
    return header, sections


def save_p8(path, header, sections):
    out = list(header)
    while out and out[-1] == "":
        out.pop()
    order = [s for s in SECTION_ORDER if s in sections] + [s for s in sections if s not in SECTION_ORDER]
    for s in order:
        out.append(s)
        out.extend(sections[s])
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def code_of(path):
    _, sections = load_p8(path)
    return "\n".join(sections.get("__lua__", []))


# ----------------------------------------------------------------------------- headless
def _find_pico8():
    if not os.path.exists(PICO8_EXE):
        raise FileNotFoundError(f"pico8.exe not found at {PICO8_EXE} (set PICO8_EXE env var)")
    return PICO8_EXE


def run_headless(cart_path, driver_lua, timeout=60, extra_code=None):
    """Append driver_lua to the cart's code and run it with `pico8 -x`.

    Returns dict(output, error, exit, seconds). Output has the "INFO: " prefixes
    stripped and the RUNNING line removed. `extra_code` replaces the cart code
    entirely (used by simulate_cart after patching).
    """
    exe = _find_pico8()
    header, sections = load_p8(cart_path)
    code = extra_code if extra_code is not None else "\n".join(sections.get("__lua__", []))
    # once the driver has run, drop the callbacks so pico8 -x exits instead of starting its game loop
    driver_lua += "\n_init=nil _update=nil _update60=nil _draw=nil\n"
    full = code + "\n-->8\n-- headless driver\n" + driver_lua
    sections["__lua__"] = full.split("\n")
    tab_starts = [0] + [i + 1 for i, ln in enumerate(full.split("\n")) if ln.startswith("-->8")]
    code_lines = code.count("\n") + 1
    TMP_DIR.mkdir(exist_ok=True)
    tmp = TMP_DIR / f"headless_{os.getpid()}_{int(time.time()*1000)}.p8"
    save_p8(tmp, header, sections)

    proc = subprocess.Popen([exe, "-x", str(tmp)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", cwd=str(TMP_DIR))
    lines, error = [], None
    t0 = time.time()

    def reader():
        nonlocal error
        for raw in proc.stdout:
            ln = raw.rstrip("\n")
            if ln.startswith("RUNNING:"):
                continue
            if re.match(r"(runtime error|syntax error)", ln):
                error = ln
            lines.append(ln[6:] if ln.startswith("INFO: ") else ln)
            if error is not None and len(lines) >= 40 + lines.index(error):
                break

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while proc.poll() is None and time.time() - t0 < timeout:
        if error is not None:
            time.sleep(0.3)   # let the error detail lines land
            break
        time.sleep(0.05)
    timed_out = proc.poll() is None and error is None
    if proc.poll() is None:
        proc.kill()
    th.join(timeout=2)
    try:
        tmp.unlink()
    except OSError:
        pass
    if timed_out:
        error = (f"timed out after {timeout}s - the driver never finished. Causes: a loop above 32767 "
                 f"iterations (wraps negative), an infinite loop, or a game that never reaches stop_when.")
    elif error:
        # "runtime error line L tab T" -> absolute line in the assembled code / cart
        m = re.search(r"line (\d+) tab (\d+)", error)
        if m:
            ln, tab = int(m.group(1)), int(m.group(2))
            if tab < len(tab_starts):
                abs_ln = tab_starts[tab] + ln
                where = f"cart code line {abs_ln}" if abs_ln <= code_lines else f"driver/setup line {abs_ln - code_lines - 2}"
                error += f"  [{where}]"
    return {"output": "\n".join(lines), "error": error, "seconds": round(time.time() - t0, 2)}


def _closest_lines(code, old, n=3):
    """Cart lines most similar to the first line of a failed patch - shows what the text looks like NOW."""
    import difflib
    want = old.strip().split("\n")[0].strip()
    if not want:
        return []
    cands = [l for l in code.split("\n") if l.strip()]
    hits = difflib.get_close_matches(want, [l.strip() for l in cands], n=n, cutoff=0.5)
    return [l for l in cands if l.strip() in hits][:n]


def apply_patches(code, patches):
    """patches: list of {old, new} exact string replacements; each old must match exactly once."""
    for i, p in enumerate(patches or []):
        old, new = p["old"], p.get("new", "")
        n = code.count(old)
        if n != 1:
            msg = f"patch {i}: expected exactly 1 match for {old!r}, found {n}."
            if n == 0:
                near = _closest_lines(code, old)
                msg += (" Patches are exact text matches against the cart as it is NOW (whitespace included), so a "
                        "patch written before an edit goes stale - re-read the cart and copy the current text.")
                if near:
                    msg += " Closest lines in the cart: " + " | ".join(repr(l) for l in near)
            else:
                msg += " Include more surrounding lines so the text is unique."
            raise ValueError(msg)
        code = code.replace(old, new)
    return code


def simulate_cart(cart_path, seconds, setup_lua="", log_every=30, log_lua="", patches=None,
                  call_draw=False, stop_when="", timeout=120, fps=None):
    """Run the game loop headless for `seconds` of game time.

    The cart's _init is called, then setup_lua runs, then _update (or _update60)
    is called fps times per simulated second. Every log_every seconds the Lua
    expression log_lua is evaluated and printed with a [m:ss] prefix. stop_when
    is a Lua condition that ends the run early. The cart's _update/_draw are
    renamed so pico8 -x doesn't start its own game loop.
    """
    code = apply_patches(code_of(cart_path), patches)
    has60 = re.search(r"function\s+_update60\s*\(", code) is not None
    fps = fps or (60 if has60 else 30)
    upd = "_update60" if has60 else "_update"
    if not re.search(rf"function\s+{upd}\s*\(", code):
        raise ValueError(f"cart defines no {upd}()")
    # rename definitions AND any calls the cart makes itself (speed hacks etc.); \b keeps __sim_* intact
    code = re.sub(r"\b_update60\b|\b_update\b", "__sim_update", code)
    code = re.sub(r"\b_draw\b", "__sim_draw", code)
    has_draw = re.search(r"function\s+__sim_draw\s*\(", code) is not None

    seconds = int(seconds)
    log_expr = log_lua.strip() or '""'
    stop = stop_when.strip() or "false"
    draw = " __sim_draw()" if (call_draw and has_draw) else ""
    driver = f"""
function __sim_tm(s) return flr(s/60)..":"..(s%60<10 and "0" or "")..s%60 end
function __sim_log(tag) printh("["..__sim_tm(__sim_s).."] "..tag..tostr({log_expr})) end
if _init then _init() end
{setup_lua}
__sim_s=0 __sim_done=false
for __m=1,{(seconds + 59) // 60} do
 for __sec=1,60 do
  if not __sim_done and __sim_s<{seconds} then
   for __f=1,{fps} do __sim_update(){draw} end
   __sim_s+=1
   if __sim_s%{max(1, int(log_every))}==0 then __sim_log("") end
   if {stop} then __sim_done=true __sim_log("STOP ") end
  end
 end
end
__sim_log("END ")
printh("cpu="..stat(1).." mem="..stat(0))
"""
    return run_headless(cart_path, driver, timeout=timeout, extra_code=code)


# ----------------------------------------------------------------------------- run / capture (Windows)
IS_WIN = os.name == "nt"


def _kill_pico8():
    if IS_WIN:
        r = subprocess.run(["taskkill", "/IM", "pico8.exe", "/F"], capture_output=True)
    else:
        r = subprocess.run(["pkill", "-f", "pico8"], capture_output=True)
    return r.returncode == 0


def run_cart(cart_path, width=1024, height=1024, restart=True):
    exe = _find_pico8()
    if restart:
        _kill_pico8()
        time.sleep(0.3)
    kw = {"close_fds": True, "cwd": str(Path(cart_path).resolve().parent)}
    if IS_WIN:
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([exe, "-width", str(width), "-height", str(height), "-run", str(Path(cart_path).resolve())], **kw)
    return proc.pid


def stop_cart():
    return "stopped" if _kill_pico8() else "no pico8 process was running"


_user32 = None


def _u32():
    global _user32
    if not IS_WIN:
        raise RuntimeError("send_keys/capture_game drive the window through Win32 and are Windows-only; "
                           "use simulate_cart for headless testing on this platform")
    if _user32 is None:
        _user32 = ctypes.windll.user32
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass
    return _user32


def _find_window(title_substr="PICO-8"):
    u = _u32()
    found = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                if title_substr.lower() in buf.value.lower():
                    found.append((hwnd, buf.value))
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else (None, None)


# vk, scancode, extended
KEYMAP = {
    "x": (0x58, 0x2D, 0), "z": (0x5A, 0x2C, 0), "c": (0x43, 0x2E, 0), "v": (0x56, 0x2F, 0),
    "up": (0x26, 0x48, 1), "down": (0x28, 0x50, 1), "left": (0x25, 0x4B, 1), "right": (0x27, 0x4D, 1),
    "enter": (0x0D, 0x1C, 0), "esc": (0x1B, 0x01, 0), "p": (0x50, 0x19, 0), "space": (0x20, 0x39, 0),
    "r": (0x52, 0x13, 0), "f6": (0x75, 0x40, 0),
    "w": (0x57, 0x11, 0), "a": (0x41, 0x1E, 0), "s": (0x53, 0x1F, 0), "d": (0x44, 0x20, 0),
    "q": (0x51, 0x10, 0), "e": (0x45, 0x12, 0), "shift": (0x10, 0x2A, 0), "ctrl": (0x11, 0x1D, 0),
}


def _key(vk, sc, ext, down):
    fl = (0x1 if ext else 0) | (0 if down else 0x2)
    _u32().keybd_event(vk, sc, fl, 0)


def _focus(hwnd):
    u = _u32()
    _key(0x12, 0x38, 0, True)
    _key(0x12, 0x38, 0, False)   # alt tap lets a background process take foreground
    u.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    return u.GetForegroundWindow() == hwnd


def send_keys(keys, hold_ms=80, gap_ms=120):
    """keys: space separated names from KEYMAP; 'wait:N' sleeps N ms; 'hold:name:N' holds a key N ms."""
    hwnd, title = _find_window()
    if not hwnd:
        raise RuntimeError("no PICO-8 window found - call run_cart first")
    ok = _focus(hwnd)
    sent = []
    for tok in keys.split():
        if tok.startswith("wait:"):
            time.sleep(int(tok[5:]) / 1000)
            continue
        hold = hold_ms
        if tok.startswith("hold:"):
            _, tok, ms = tok.split(":")
            hold = int(ms)
        if tok not in KEYMAP:
            raise ValueError(f"unknown key {tok!r}; known: {', '.join(KEYMAP)}, wait:MS, hold:KEY:MS")
        vk, sc, ext = KEYMAP[tok]
        _key(vk, sc, ext, True)
        time.sleep(hold / 1000)
        _key(vk, sc, ext, False)
        time.sleep(gap_ms / 1000)
        sent.append(tok)
    return {"window": title, "focused": ok, "sent": sent}


def capture_window(max_size=512):
    """Screenshot the PICO-8 window's client area, downscaled; returns PNG bytes."""
    from PIL import ImageGrab
    hwnd, title = _find_window()
    if not hwnd:
        raise RuntimeError("no PICO-8 window found - call run_cart first")
    u = _u32()

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rc = RECT()
    u.GetClientRect(hwnd, ctypes.byref(rc))
    pt = POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    box = (pt.x, pt.y, pt.x + rc.r, pt.y + rc.b)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    if max_size and max(img.size) > max_size:
        img = img.resize((max_size, int(img.size[1] * max_size / img.size[0])))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), title, img.size


# ----------------------------------------------------------------------------- gfx
def _ensure_rows(sections, key, count, width):
    rows = sections.setdefault(key, [])
    rows[:] = [r.ljust(width, "0")[:width] for r in rows]
    while len(rows) < count:
        rows.append("0" * width)


def sprite_cells(index, w=8, h=8):
    """Sheet cell indices covered by a w x h block whose top-left cell is `index`."""
    cw, ch = w // 8, h // 8
    col, row = index % 16, index // 16
    return [(row + dy) * 16 + (col + dx) for dy in range(ch) for dx in range(cw)]


def _cell_is_empty(g, cell):
    return all(ch == "0" for ch in "".join(get_sprite_rows({"__gfx__": g}, cell)))


def set_sprite(cart_path, index, rows, overwrite=False):
    """rows: list of hex strings. Width/height may be multiples of 8; the block is
    written with its top-left at sprite `index` on the 16-wide sheet.

    A block wider or taller than 8 spills into the neighbouring cells (a 16x16 at
    index 0 covers cells 0, 1, 16 and 17). An 8x8 write always replaces its one
    cell, but a multi-cell block is refused if ANY covered cell already holds
    pixels unless overwrite=True - this is the classic mistake of placing 16x16
    sprites at consecutive indices and shredding them. Redrawing an existing
    16x16 therefore needs overwrite=True; the error says so."""
    rows = [r.strip().lower() for r in rows if r.strip() != ""]
    if not rows:
        raise ValueError("no rows")
    w = len(rows[0])
    if any(len(r) != w for r in rows):
        raise ValueError("all rows must have the same length")
    if w % 8 or len(rows) % 8:
        raise ValueError("sprite block must be a multiple of 8x8")
    if not all(c in "0123456789abcdef" for r in rows for c in r):
        raise ValueError("rows may only contain hex digits 0-f (palette index)")
    h = len(rows)
    x0, y0 = (index % 16) * 8, (index // 16) * 8
    if x0 + w > 128 or y0 + h > 128:
        raise ValueError(f"a {w}x{h} block at index {index} runs off the 128x128 sheet "
                         f"(column {index % 16} + {w // 8} cells wide, row {index // 16} + {h // 8} cells tall)")
    header, sections = load_p8(cart_path)
    _ensure_rows(sections, "__gfx__", 128, 128)
    g = sections["__gfx__"]
    cells = sprite_cells(index, w, h)
    cw, ch = w // 8, h // 8
    if not overwrite and len(cells) > 1:
        busy = [c for c in cells if not _cell_is_empty(g, c)]
        if busy:
            raise ValueError(
                f"a {w}x{h} block at index {index} covers sheet cells {cells}, and {busy} already contain pixels. "
                f"If those belong to a different sprite, writing here would corrupt it: a {w}x{h} sprite uses {cw} "
                f"column(s) and {ch} row(s) of the 16-wide sheet, so keep {cw} indices between sprites on a row "
                f"(e.g. {index}, {index + cw}, {index + 2 * cw}) and {16 * ch} between rows. Pick an index whose "
                f"{len(cells)} cells are all empty (render_gfx shows the sheet). If you are redrawing the sprite that "
                f"already lives at index {index}, call again with overwrite=true.")
    for i, r in enumerate(rows):
        y = y0 + i
        g[y] = g[y][:x0] + r + g[y][x0 + w:]
    save_p8(cart_path, header, sections)
    out = {"index": index, "x": x0, "y": y0, "w": w, "h": h, "cells": cells,
           "draw_with": f"spr({index}, x, y" + (f", {cw}, {ch})" if (cw, ch) != (1, 1) else ")")}
    if (cw, ch) != (1, 1):
        out["next_free_index_on_this_row"] = index + cw
        out["note"] = (f"this sprite occupies {cw}x{ch} sheet cells {cells}; render_gfx with size={max(w, h)} "
                       f"to see it whole (in the default 8x8 view it appears split across those cells, which is normal)")
    return out


def get_sprite_rows(sections, index, w=8, h=8):
    g = sections.get("__gfx__", [])
    x0, y0 = (index % 16) * 8, (index // 16) * 8
    out = []
    for y in range(y0, y0 + h):
        row = g[y] if y < len(g) else ""
        out.append(row.ljust(128, "0")[x0:x0 + w])
    return out


def render_gfx(cart_path, sprites="0-15", scale=8, transparent0=True, size=8):
    """Return PNG bytes of the given sprites side by side (with index labels).

    size=8 renders each index as one 8x8 cell. size=16/24/32 treats each index as
    the top-left of a size x size block and draws it as a single labelled tile -
    use this for multi-cell sprites, otherwise they look "split"."""
    from PIL import Image, ImageDraw
    if size % 8 or not 8 <= size <= 128:
        raise ValueError("size must be 8, 16, 24 ... 128")
    _, sections = load_p8(cart_path)
    idx = []
    for part in sprites.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            idx.extend(range(int(a), int(b) + 1))
        else:
            idx.append(int(part))
    if not idx:
        raise ValueError("no sprite indices")
    for i in idx:
        if not 0 <= i <= 255:
            raise ValueError(f"sprite index {i} out of range 0-255")
        if (i % 16) * 8 + size > 128 or (i // 16) * 8 + size > 128:
            raise ValueError(f"a {size}x{size} block at index {i} runs off the sheet; for {size}-px sprites list "
                             f"top-left indices only (e.g. 0, {size // 8}, {2 * size // 8} ...)")
    cell = size * scale + 4
    per_row = min(len(idx), max(1, 128 // size))
    rows = (len(idx) + per_row - 1) // per_row
    img = Image.new("RGB", (per_row * cell, rows * (cell + 10)), (40, 40, 40))
    d = ImageDraw.Draw(img)
    for n, i in enumerate(idx):
        cx, cy = (n % per_row) * cell + 2, (n // per_row) * (cell + 10) + 10
        for y, row in enumerate(get_sprite_rows(sections, i, size, size)):
            for x, ch in enumerate(row):
                c = int(ch, 16)
                if c == 0 and transparent0:
                    col = (60, 60, 60) if (x + y) % 2 else (80, 80, 80)
                else:
                    col = PALETTE[c]
                d.rectangle([cx + x * scale, cy + y * scale, cx + (x + 1) * scale - 1, cy + (y + 1) * scale - 1], fill=col)
        d.text((cx, cy - 10), str(i), fill=(220, 220, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), idx


# ----------------------------------------------------------------------------- sfx / music
def pitch(name):
    """'a4' -> pico pitch number (a4 = 33 = 440Hz); accepts sharps/flats; c2..b7 range"""
    m = re.fullmatch(r"([a-g](?:#|b)?)(\d)", name.lower())
    if not m:
        raise ValueError(f"bad note {name!r} (expected e.g. c4, f#3, bb5)")
    p = (int(m.group(2)) - 2) * 12 + SEMI[m.group(1)]
    if not 0 <= p <= 63:
        raise ValueError(f"note {name} out of PICO-8 range (c2..d#7)")
    return p


def _note_hex(p, wave, vol, fx=0):
    for v, hi, nm in ((p, 63, "pitch"), (wave, 15, "wave"), (vol, 7, "volume"), (fx, 7, "effect")):
        if not 0 <= v <= hi:
            raise ValueError(f"{nm} {v} out of range 0..{hi}")
    return f"{p:02x}{wave:x}{vol:x}{fx:x}"


def parse_notes(spec, wave, vol, fx=0):
    """'c4:2 e4:2 r:1 g4:4:1:7:2' -> list of 5-char note strings.
    token = note[:len[:wave[:vol[:fx]]]]; note 'r' = rest; len in steps (default 1)."""
    out = []
    for tok in spec.split():
        parts = tok.split(":")
        nm = parts[0]
        ln = int(parts[1]) if len(parts) > 1 and parts[1] else 1
        w = int(parts[2]) if len(parts) > 2 and parts[2] else wave
        v = int(parts[3]) if len(parts) > 3 and parts[3] else vol
        f = int(parts[4]) if len(parts) > 4 and parts[4] else fx
        if nm in ("r", "-", "."):
            out += ["00000"] * ln
        else:
            out += [_note_hex(pitch(nm), w, v, f)] * ln
    if len(out) > 32:
        raise ValueError(f"{len(out)} steps - an sfx holds at most 32")
    return out


def set_sfx(cart_path, index, notes="", speed=16, wave=1, volume=5, effect=0,
            loop_start=None, loop_end=0, raw_notes=None):
    """Write sfx `index`. notes = spec string (see parse_notes) or raw_notes = list of
    [pitch, wave, vol, fx]. If loop_start is None and the sfx is shorter than 32 steps,
    loop_start is set to its length with loop_end 0, which makes PICO-8 stop there
    (short sound effect). For music, pass loop_start=0 explicitly."""
    if not 0 <= index <= 63:
        raise ValueError("sfx index 0..63")
    if not 0 <= speed <= 255:
        raise ValueError("speed 0..255 (1 = fastest, 16 ~ typical melody, 4-8 typical effects)")
    if raw_notes:
        steps = [_note_hex(*[int(x) for x in n]) for n in raw_notes]
    else:
        steps = parse_notes(notes, wave, volume, effect)
    n = len(steps)
    if loop_start is None:
        loop_start = n if n < 32 else 0
    steps += ["00000"] * (32 - n)
    row = f"00{speed:02x}{loop_start:02x}{loop_end:02x}" + "".join(steps)
    assert len(row) == 168
    header, sections = load_p8(cart_path)
    _ensure_rows(sections, "__sfx__", 64, 168)
    sections["__sfx__"][index] = row
    save_p8(cart_path, header, sections)
    dur = n * speed / 120  # each step is speed/120 s (speed 1 = 1/120 s)
    return {"index": index, "steps": n, "speed": speed, "loop_start": loop_start, "loop_end": loop_end,
            "approx_seconds": round(dur, 2), "row": row}


def set_music(cart_path, pattern, channels, loop_start=False, loop_end=False, stop=False):
    """channels: 4 entries, each an sfx index 0..63 or null for silent."""
    if not 0 <= pattern <= 63:
        raise ValueError("pattern 0..63")
    if len(channels) != 4:
        raise ValueError("channels must have exactly 4 entries (use null to mute a channel)")
    flags = (1 if loop_start else 0) | (2 if loop_end else 0) | (4 if stop else 0)
    parts = []
    for i, c in enumerate(channels):
        if c is None:
            parts.append(f"{0x41 + i:02x}")
        else:
            c = int(c)
            if not 0 <= c <= 63:
                raise ValueError("sfx index 0..63")
            parts.append(f"{c:02x}")
    row = f"{flags:02x} " + "".join(parts)
    header, sections = load_p8(cart_path)
    rows = sections.setdefault("__music__", [])
    while len(rows) < 64:
        rows.append("00 41424344")
    rows[pattern] = row
    save_p8(cart_path, header, sections)
    return {"pattern": pattern, "row": row}


def describe_data(cart_path):
    """Human-readable summary of the data sections."""
    _, sections = load_p8(cart_path)
    g = sections.get("__gfx__", [])
    used = []
    for i in range(256):
        if any(ch != "0" for r in get_sprite_rows(sections, i) for ch in r):
            used.append(i)
    sfx = []
    for i, row in enumerate(sections.get("__sfx__", [])):
        if len(row) == 168 and row[8:] != "0" * 160:
            speed, ls, le = int(row[2:4], 16), int(row[4:6], 16), int(row[6:8], 16)
            notes = [row[8 + k * 5: 13 + k * 5] for k in range(32)]
            n = sum(1 for x in notes if x != "00000")
            sfx.append(f"  sfx {i}: speed {speed} loop {ls}-{le} notes {n}")
    music = []
    for i, row in enumerate(sections.get("__music__", [])):
        m = re.fullmatch(r"([0-9a-f]{2}) ([0-9a-f]{8})", row)
        if m:
            fl = int(m.group(1), 16)
            ch = [int(m.group(2)[k:k + 2], 16) for k in (0, 2, 4, 6)]
            if any(c < 0x40 for c in ch):
                chs = ["-" if c >= 0x40 else str(c) for c in ch]
                fls = "".join(x for x, b in (("L", 1), ("E", 2), ("S", 4)) if fl & b)
                music.append(f"  pattern {i}: channels {'/'.join(chs)} {fls}")
    return "\n".join([
        f"sprites used ({len(used)}): {', '.join(map(str, used)) if used else 'none'}",
        f"sfx defined: {len(sfx)}", *sfx,
        f"music patterns defined: {len(music)}", *music,
        f"map rows: {len(sections.get('__map__', []))}",
    ])
