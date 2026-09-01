"""Exercise p8tools against a cart (default: examples/demo.p8). Needs pico8 installed.

    uv run python test_p8tools.py [path/to/cart.p8]
"""
import shutil, sys, time
from pathlib import Path
import p8tools

HERE = Path(__file__).parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "examples" / "demo.p8"
TMP = HERE / "tmp" / "test_copy.p8"
TMP.parent.mkdir(exist_ok=True)
shutil.copy(SRC, TMP)
fails = 0


def check(label, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + label + (f"  {detail}" if detail else ""))
    fails += 0 if ok else 1


print("--- describe_data")
print(p8tools.describe_data(TMP))

r = p8tools.run_headless(TMP, 'assert(tms(90)=="1:30") printh("tms ok")', timeout=20)
check("run_headless unit check", r["error"] is None and "tms ok" in r["output"], str(r))

r = p8tools.run_headless(TMP, 'local q=nil q.x=1', timeout=20)
check("run_headless reports runtime errors", r["error"] is not None and "runtime error" in r["error"], r["error"])

t0 = time.time()
r = p8tools.simulate_cart(TMP, seconds=120, log_every=30,
                          log_lua='"kills="..kills.." hits="..hits.." e="..#e',
                          stop_when='st=="over"', timeout=60)
check("simulate_cart 2 min", r["error"] is None and "END" in r["output"], f"{round(time.time()-t0,1)}s wall")
print(r["output"])

patches = [{"old": "function hurt()\n hp-=1 hits+=1", "new": "function hurt()\n hits+=1"}]
r = p8tools.simulate_cart(TMP, seconds=60, log_every=60, log_lua='"hits="..hits.." hp="..hp', patches=patches, timeout=60)
check("simulate_cart with god-mode patch", r["error"] is None and "hp=3" in r["output"], r["output"].split("\n")[0])

try:
    p8tools.simulate_cart(TMP, seconds=5, patches=[{"old": "does not exist", "new": ""}])
    check("bad patch rejected", False)
except ValueError as e:
    check("bad patch rejected", True, str(e))

r = p8tools.set_sfx(TMP, 60, "c4:2 e4:2 g4:2 c5:4", speed=8, wave=2, volume=5)
check("set_sfx short effect", r["steps"] == 10 and r["loop_start"] == 10 and len(r["row"]) == 168)
r = p8tools.set_sfx(TMP, 61, "a3:4 a3:4 e3:4 e3:4 f3:4 f3:4 g3:4 g3:4", speed=12, loop_start=0)
check("set_sfx music phrase", r["steps"] == 32)
r = p8tools.set_music(TMP, 40, [61, None, None, None], loop_start=True, loop_end=True)
check("set_music", r["row"] == "03 3d424344", r["row"])
r = p8tools.set_sprite(TMP, 32, ["00000000", "00888800", "08888880", "88800888", "88800888", "08888880", "00888800", "00000000"])
check("set_sprite", r["x"] == 0 and r["y"] == 16)
png, idx = p8tools.render_gfx(TMP, "0-3,32", scale=6)
(HERE / "tmp" / "gfx.png").write_bytes(png)
check("render_gfx", len(png) > 500 and idx == [0, 1, 2, 3, 32], f"{len(png)} bytes -> tmp/gfx.png")
d = p8tools.describe_data(TMP)
check("describe_data sees edits", "32" in d.split("\n")[0] and "sfx 60" in d and "pattern 40" in d)

r = p8tools.simulate_cart(TMP, seconds=5, log_every=5, log_lua='"e="..#e', timeout=30)
check("cart still runs after data edits", r["error"] is None)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
