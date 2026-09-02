---
name: pico8-dev
description: Build, test and balance PICO-8 games (.p8 carts) using the pico8 MCP server - headless simulation, validation, sprite/sfx/music authoring, run + screenshot. Use whenever the user mentions PICO-8 or a .p8 cart.
---

# PICO-8 game development

Carts are plain text `.p8` files. Edit the `__lua__` section with normal file tools and use the `pico8` MCP tools
for everything else. Never hand-type `__gfx__` / `__sfx__` / `__music__` hex.

## The loop

1. **Edit** the Lua in the `.p8` directly.
2. **`validate_cart`** after every edit. Watch tokens (8192 cap). Default lint hides bare-global noise; what is
   left is real. It can NOT catch runtime nil errors - that is what step 3 is for.
3. **`simulate_cart`** - run the game headless for its full intended length before declaring anything done.
   It calls `_init()`, your `setup_lua` (e.g. `newgame() st="play"`), then `_update()` for N game-seconds and
   prints `log_lua` every `log_every` seconds. A 25-minute game takes about a minute of wall clock. Use it to:
   - catch crashes (`runtime error line L tab T [cart code line N]` - fix and rerun)
   - measure pacing: level per minute, kills/sec, enemies + bullets on screen, boss HP over time, hits taken
   - make the game play itself with `patches` (exact string replacements, each must match exactly once):
     god mode (the hurt function counts `hits` instead of dying), an autopilot replacing the `btn()` block,
     auto-pick for level-up menus, `stop_when='st=="win" or st=="over"'`
4. **Art / sound**: `set_sprite` (rows of hex digits), `render_gfx` to eyeball them, `set_sfx` with note names
   (`"c4:2 e4:2 g4:4 r:2"`), `set_music` to arrange sfx into 4-channel patterns. `read_cart` summarises what
   is defined.
   - **16x16 sprites take FOUR sheet cells** (`n, n+1, n+16, n+17`) and are drawn with `spr(n,x,y,2,2)`. Lay them
     out at 0, 2, 4 ... 14 then 32, 34 ... - never at consecutive indices. `set_sprite` refuses to write a
     multi-cell block over occupied cells (pass `overwrite=true` only when redrawing that same sprite), and
     returns the `cells` + `draw_with` call. In `render_gfx` a 16x16 looks like four labelled quarters - that is
     normal; pass `size=16` with the top-left indices to see it whole.
5. **Look at it** only when needed: `run_cart`, `capture_game` (returns screenshots, can send keys first),
   `send_keys`, `stop_cart`. `run_cart` kills any running PICO-8 by default - if the user may be playing, pass
   `restart=false` (opens a second window) or use simulate_cart; never send_keys into their game.
   `simulate_cart` has no input at all (`btn`/`btnp` are always false), so autopilots must bypass them, and
   `patches` match exact current text - re-read the cart before patching after any edit.

## Hard rules learned the expensive way

- **Numbers wrap above 32767** (16.16 fixed point). A frame counter goes negative at 18:12 and takes boss HP,
  timers and scores with it. Keep game time in *seconds* (`if t%30==0 then gs+=1 end`), keep scores small, never
  loop past 32767 iterations.
- 8192 tokens: music and art go in the data sections, not Lua. Tables of data (`{name,pool,color}`) are cheap.
- Every global used in `_update` must be initialised in the reset function; lint won't tell you, simulate_cart
  will crash on frame 1 and tell you.
- `-->8` separates editor tabs; PICO-8 error messages count lines within a tab (the tools translate them).
- Buttons: `btn(0..3)` arrows, `btn(4)` O = Z/C, `btn(5)` X = X/V. `btnp` repeats when held.
- The screen is always 128x128; only the window scales (`-width/-height`). "Zoom out" is not possible - give the
  player space via enemy caps and slower bullets instead.

## Balance heuristics (from a 25-minute auto-shooter)

- Log per-stage hit counts; difficulty should ramp, with the peak in the penultimate stage.
- Cap live enemies (`#e<16`) - a screen full of parked turrets is the #1 "no room to dodge" complaint.
- First level-up within 20-30 s; XP curve cheap early, steep late; the player should end with roughly half of
  all upgrade slots so choices matter.
- Boss fights 40-90 s; scale enemy HP with a capped multiplier, not unbounded.
- First 10-20 s: fodder only, no shooting.
