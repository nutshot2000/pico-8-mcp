"""Exercise p8tools against a cart (default: examples/demo.p8). Needs pico8 installed.

    uv run python test_p8tools.py [path/to/cart.p8]
"""
import os, shutil, sys, time
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
    check("bad patch rejected", "re-read the cart" in str(e), str(e)[:80])
try:
    p8tools.simulate_cart(TMP, seconds=5, patches=[{"old": "function hurt()\n  hp-=1 hits+=1", "new": ""}])
    check("stale patch suggests current text", False)
except ValueError as e:
    check("stale patch suggests current text", "function hurt()" in str(e).split("Closest lines")[-1], str(e)[-90:])

# --- sandboxed saves, scripted input, batches, seeds
IO_CART = HERE / "tmp" / "io_test.p8"
IO_CART.write_text("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
                   "function _init() cartdata(\"p8mcp_sandbox_test\") n=dget(0)+1 dset(0,n) x=0 taps=0 r=rnd(1000)\\1 end\n"
                   "function _update() if btn(1) then x+=1 end if btnp(5) then taps+=1 end end\n", encoding="utf-8")
cdata = Path(os.environ.get("APPDATA", "")) / "pico-8" / "cdata" / "p8mcp_sandbox_test.p8d.txt"
if cdata.exists():
    cdata.unlink()
r = p8tools.simulate_cart(IO_CART, seconds=4, runs=3, summary_lua="n", timeout=30)
check("sandboxed cartdata persists across runs in memory", r.get("summary", {}).get("values") == [1, 2, 3], str(r.get("summary")))
check("sandboxed cartdata never touches the real save file", not cdata.exists(), str(cdata))
r = p8tools.simulate_cart(IO_CART, seconds=4, inputs_lua="press(1) if __sim_s%2==0 then press(5) end",
                          log_every=4, log_lua='"x="..x.." taps="..taps', timeout=30)
check("inputs_lua drives btn() and edge-only btnp()", "x=120 taps=2" in r["output"], r["output"].split("\n")[0])
check("peak per-frame cpu reported", "peak frame cpu=" in r["output"], r["output"].split("\n")[-1])
a = p8tools.simulate_cart(IO_CART, seconds=1, runs=3, seed=7, summary_lua="r", timeout=30)["summary"]["values"]
b = p8tools.simulate_cart(IO_CART, seconds=1, runs=3, seed=7, summary_lua="r", timeout=30)["summary"]["values"]
check("seed makes batches reproducible", a == b and len(set(a)) > 1, f"{a} vs {b}")

r = p8tools.set_sfx(TMP, 60, "c4:2 e4:2 g4:2 c5:4", speed=8, wave=2, volume=5)
check("set_sfx short effect", r["steps"] == 10 and r["loop_start"] == 10 and len(r["row"]) == 168)
r = p8tools.set_sfx(TMP, 61, "a3:4 a3:4 e3:4 e3:4 f3:4 f3:4 g3:4 g3:4", speed=12, loop_start=0)
check("set_sfx music phrase", r["steps"] == 32)
r = p8tools.set_music(TMP, 40, [61, None, None, None], loop_start=True, loop_end=True)
check("set_music", r["row"] == "03 3d424344", r["row"])
r = p8tools.set_sprite(TMP, 32, ["00000000", "00888800", "08888880", "88800888", "88800888", "08888880", "00888800", "00000000"])
check("set_sprite", r["x"] == 0 and r["y"] == 16 and r["cells"] == [32] and r["draw_with"] == "spr(32, x, y)")
big = ["c" * 16] * 16
r = p8tools.set_sprite(TMP, 64, big)
check("set_sprite 16x16", r["cells"] == [64, 65, 80, 81] and r["draw_with"] == "spr(64, x, y, 2, 2)"
      and r["next_free_index_on_this_row"] == 66, str(r))
for bad in (65, 80, 81, 49, 48):
    try:
        p8tools.set_sprite(TMP, bad, big)
        check(f"16x16 at {bad} overlapping 64 rejected", False)
    except ValueError as e:
        check(f"16x16 at {bad} overlapping 64 rejected", "already contain pixels" in str(e), str(e)[:90])
r = p8tools.set_sprite(TMP, 66, big)
check("16x16 at 66 (correct stride) accepted", r["cells"] == [66, 67, 82, 83])
r = p8tools.set_sprite(TMP, 96, big)
check("16x16 at 96 (row below, stride 32) accepted", r["cells"] == [96, 97, 112, 113])
try:
    p8tools.set_sprite(TMP, 64, [("a" * 16)] * 16)
    check("redrawing an existing 16x16 needs overwrite", False)
except ValueError as e:
    check("redrawing an existing 16x16 needs overwrite", "overwrite=true" in str(e))
r = p8tools.set_sprite(TMP, 64, [("a" * 16)] * 16, overwrite=True)
check("overwrite=True redraws it", r["cells"] == [64, 65, 80, 81] and p8tools.get_sprite_rows(p8tools.load_p8(TMP)[1], 81)[0] == "a" * 8)
r = p8tools.set_sprite(TMP, 65, ["00000000", "00888800", "08888880", "88800888", "88800888", "08888880", "00888800", "00000000"])
check("8x8 always replaces its single cell", r["cells"] == [65])
try:
    p8tools.set_sprite(TMP, 15, big)
    check("16x16 at column 15 runs off sheet", False)
except ValueError as e:
    check("16x16 at column 15 runs off sheet", "runs off" in str(e))
png, idx = p8tools.render_gfx(TMP, "0-3,32", scale=6)
(HERE / "tmp" / "gfx.png").write_bytes(png)
check("render_gfx", len(png) > 500 and idx == [0, 1, 2, 3, 32], f"{len(png)} bytes -> tmp/gfx.png")
png16, idx = p8tools.render_gfx(TMP, "64,66", scale=4, size=16)
(HERE / "tmp" / "gfx16.png").write_bytes(png16)
check("render_gfx size=16", len(png16) > 200 and idx == [64, 66], "-> tmp/gfx16.png")
try:
    p8tools.render_gfx(TMP, "15", size=16)
    check("render_gfx size=16 off-sheet index rejected", False)
except ValueError as e:
    check("render_gfx size=16 off-sheet index rejected", "top-left indices" in str(e))
d = p8tools.describe_data(TMP)
check("describe_data sees edits", "32" in d.split("\n")[0] and "sfx 60" in d and "pattern 40" in d)

r = p8tools.simulate_cart(TMP, seconds=5, log_every=5, log_lua='"e="..#e', timeout=30)
check("cart still runs after data edits", r["error"] is None)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
