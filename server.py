#!/usr/bin/env python3
"""PICO-8 MCP Server - Tools for working with PICO-8 carts."""

import sys
import os
import re
import difflib
import urllib.request
from pathlib import Path
from glob import glob

# Add shrinko8 to path
sys.path.insert(0, str(Path(__file__).parent / "shrinko8"))

from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, Resource, ResourceTemplate
import mcp.server.stdio
import base64
import json
import p8tools

# Import shrinko8 modules
from pico_cart import read_cart, write_cart
from pico_tokenize import tokenize, count_tokens
from pico_parse import parse
from pico_process import Source, process_code, PicoContext
from pico_compress import write_compressed_size
from pico_lint import lint_code

app = Server("pico8-mcp-server")

@app.list_resources()
async def list_resources() -> list[Resource]:
    """List available PICO-8 documentation resources."""
    resources = []

    # Add PICO-8 API documentation if it exists
    docs_dir = Path(__file__).parent / "docs"
    if docs_dir.exists():
        for doc_file in docs_dir.glob("*.txt"):
            resources.append(Resource(
                uri=f"pico8://docs/{doc_file.stem}",
                name=f"PICO-8: {doc_file.stem}",
                mimeType="text/plain",
                description=f"PICO-8 documentation: {doc_file.stem}"
            ))

    return resources

@app.read_resource()
async def read_resource(uri: str) -> str:
    """Read PICO-8 documentation resource."""
    if not uri.startswith("pico8://docs/"):
        raise ValueError(f"Unknown resource URI: {uri}")

    doc_name = uri.replace("pico8://docs/", "")
    doc_path = Path(__file__).parent / "docs" / f"{doc_name}.txt"

    if not doc_path.exists():
        raise ValueError(f"Documentation not found: {doc_name}")

    return doc_path.read_text()

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available PICO-8 tools."""
    return [
        Tool(
            name="count_tokens",
            description="Count tokens, characters, and compressed size in a PICO-8 cart",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {
                        "type": "string",
                        "description": "Path to the .p8 or .p8.png cart file"
                    }
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="read_cart",
            description="Read a PICO-8 cart and return its sections (code, sprites, map, sfx, music)",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {
                        "type": "string",
                        "description": "Path to the .p8 or .p8.png cart file"
                    },
                    "section": {
                        "type": "string",
                        "description": "Section to read: 'code', 'gfx', 'map', 'sfx', 'music', or 'all'",
                        "enum": ["code", "gfx", "map", "sfx", "music", "all"]
                    }
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="validate_cart",
            description=("Validate a PICO-8 cart: token/char/compressed limits, syntax errors, and lint. "
                         "By default PICO-8-style global usage is NOT reported (carts use globals by convention); "
                         "what remains is actionable: parse errors, unused/duplicate locals, typos. "
                         "Run this after every code edit. Note it cannot catch runtime nil errors - use simulate_cart for that."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {
                        "type": "string",
                        "description": "Path to the .p8 or .p8.png cart file"
                    },
                    "lint": {
                        "type": "string",
                        "enum": ["default", "all", "none"],
                        "description": "default = hide undefined/unused/duplicate GLOBAL warnings; all = everything; none = limits + syntax only"
                    }
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="simulate_cart",
            description=(
                "Run a cart's game loop HEADLESS at full CPU speed (no window) and return printed telemetry. "
                "Calls _init(), runs setup_lua, then _update()/_update60() for `seconds` of game time, evaluating "
                "log_lua every log_every seconds. A 25-minute game simulates in a few seconds. Use it for: balance "
                "telemetry, crash detection (runtime errors are reported with line numbers), regression checks. "
                "There is no input, so patch in an autopilot / god mode with `patches` (exact string replacements on "
                "the code, each must match once) - e.g. replace the btn() block with AI, or make hurt() count hits. "
                "Remember PICO-8 numbers wrap above 32767: never let a frame counter run past 18 minutes."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "seconds": {"type": "integer", "description": "game-seconds to simulate (e.g. 1500 for a 25-minute run)"},
                    "setup_lua": {"type": "string", "description": "Lua run once after _init(), e.g. 'st=\"play\" newgame()' to skip a title screen"},
                    "log_every": {"type": "integer", "description": "seconds between log lines (default 30)"},
                    "log_lua": {"type": "string", "description": "Lua EXPRESSION (string) logged each interval, e.g. '\"lvl=\"..p.lvl..\" hp=\"..p.hp..\" enemies=\"..#e'"},
                    "stop_when": {"type": "string", "description": "Lua condition that ends the run early, e.g. 'st==\"over\" or st==\"win\"'"},
                    "patches": {"type": "array", "items": {"type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old"]},
                                "description": "exact-string code replacements applied before running (god mode, autopilot, auto-pick menus)"},
                    "call_draw": {"type": "boolean", "description": "also call _draw() each frame (slower; only if game logic lives in _draw)"},
                    "timeout": {"type": "integer", "description": "wall-clock seconds before giving up (default 120)"}
                },
                "required": ["cart_path", "seconds"]
            }
        ),
        Tool(
            name="run_headless",
            description=("Low-level: append arbitrary driver Lua to a cart's code and execute it with `pico8 -x` (headless). "
                         "printh() output is returned. Use for unit-test style checks of functions ('assert(tms(90)==\"1:30\")'). "
                         "If the cart defines _update/_draw, pico8 will start its game loop and the call will time out - "
                         "prefer simulate_cart for whole-game runs. Loops must stay below 32767 iterations."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "driver_lua": {"type": "string", "description": "Lua appended after the cart code"},
                    "timeout": {"type": "integer", "description": "seconds (default 30)"}
                },
                "required": ["cart_path", "driver_lua"]
            }
        ),
        Tool(
            name="run_cart",
            description=("Launch the cart in a real PICO-8 window (kills any running PICO-8 first). Default window 1024x1024 "
                         "(PICO-8's screen is always 128x128; only the window scales). Follow with send_keys/capture_game "
                         "to play and look at it. Prefer simulate_cart for anything measurable; use this to SEE the game."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"}
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="stop_cart",
            description="Kill the running PICO-8 window.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="send_keys",
            description=("Send key presses to the running PICO-8 window (focuses it first). keys: space-separated names: "
                         "x z c v up down left right enter esc p space r f6, plus 'wait:MS' and 'hold:KEY:MS' (e.g. 'hold:right:600'). "
                         "PICO-8 buttons: arrows, O = z/c, X = x/v. Enter/p opens the pause menu, esc exits to the console."),
            inputSchema={
                "type": "object",
                "properties": {
                    "keys": {"type": "string"},
                    "hold_ms": {"type": "integer", "description": "press duration per key (default 80)"},
                    "gap_ms": {"type": "integer", "description": "pause between keys (default 120)"}
                },
                "required": ["keys"]
            }
        ),
        Tool(
            name="capture_game",
            description=("Screenshot the running PICO-8 window and return the image (downscaled to max_size px). Optionally send keys "
                         "first and wait `delay_ms` before capturing; `count`>1 takes several shots `interval_ms` apart to see motion. "
                         "Caution: if a human is also playing, the keys and the game state you see may not be yours."),
            inputSchema={
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "optional key sequence sent before the capture (see send_keys)"},
                    "delay_ms": {"type": "integer", "description": "wait before first capture (default 500)"},
                    "count": {"type": "integer", "description": "number of screenshots (default 1, max 6)"},
                    "interval_ms": {"type": "integer", "description": "gap between shots (default 2000)"},
                    "max_size": {"type": "integer", "description": "longest edge in px (default 512)"}
                }
            }
        ),
        Tool(
            name="set_sprite",
            description=("Write a sprite into the __gfx__ sheet. rows = 8 strings of 8 hex digits (palette index 0-f, 0 = transparent), "
                         "or a larger block (16x16 = 16 rows of 16 chars) placed with its top-left at sprite `index`. "
                         "Sheet is 16 sprites wide (index = row*16 + col). Verify with render_gfx."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "index": {"type": "integer", "description": "sprite number 0-255 (0 is conventionally left blank)"},
                    "rows": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["cart_path", "index", "rows"]
            }
        ),
        Tool(
            name="render_gfx",
            description="Render sprites from the cart as an image so you can check the art. sprites: '0-15' or '1,3,7-9'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "sprites": {"type": "string", "description": "index list/ranges (default '0-15')"},
                    "scale": {"type": "integer", "description": "pixels per texel (default 8)"}
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="set_sfx",
            description=(
                "Write sound effect / music phrase `index` (0-63) from note notation - no hex needed. notes: space-separated "
                "tokens note[:len[:wave[:vol[:fx]]]], e.g. 'c4:2 e4:2 g4:4 r:2 c5:6' (r = rest, len in steps, max 32 steps total). "
                "Notes c2..d#7 (a4 = 440Hz). wave 0 sine 1 triangle 2 saw 3 square 4 pulse 5 organ 6 noise 7 phaser. vol 0-7. "
                "fx 1 slide 2 vibrato 3 drop 4 fade-in 5 fade-out 6/7 arp. speed = 1/120 s per step (16 = 8 steps/sec). "
                "Short effects: leave loop_start unset and it stops after the last note. Music phrases: fill all 32 steps and pass "
                "loop_start=0. Tip: sfx(n) in code plays it; music patterns reference sfx indices."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "index": {"type": "integer"},
                    "notes": {"type": "string"},
                    "speed": {"type": "integer"},
                    "wave": {"type": "integer"},
                    "volume": {"type": "integer"},
                    "effect": {"type": "integer"},
                    "loop_start": {"type": "integer"},
                    "loop_end": {"type": "integer"},
                    "raw_notes": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                                  "description": "alternative to notes: 32 entries of [pitch 0-63, wave, vol, fx]"}
                },
                "required": ["cart_path", "index"]
            }
        ),
        Tool(
            name="set_music",
            description=("Write music pattern `pattern` (0-63): four channels, each an sfx index or null (silent). Patterns play in "
                         "sequence from music(n); set loop_start on the first and loop_end on the last pattern of a track so it repeats; "
                         "stop=true ends playback after that pattern (fanfares). All sfx in a pattern should share a speed. "
                         "Typical layout: bass / arp / lead / drums on channels 0-3."),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {"type": "string"},
                    "pattern": {"type": "integer"},
                    "channels": {"type": "array", "items": {"type": ["integer", "null"]}, "minItems": 4, "maxItems": 4},
                    "loop_start": {"type": "boolean"},
                    "loop_end": {"type": "boolean"},
                    "stop": {"type": "boolean"}
                },
                "required": ["cart_path", "pattern", "channels"]
            }
        ),
        Tool(
            name="minify_cart",
            description="Minify a PICO-8 cart to reduce token count and compressed size",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {
                        "type": "string",
                        "description": "Path to the .p8 or .p8.png cart file to minify"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Path where the minified cart should be saved"
                    }
                },
                "required": ["cart_path", "output_path"]
            }
        ),
        Tool(
            name="analyze_cart",
            description="Analyze cart code: list functions, globals, locals, complexity metrics",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path": {
                        "type": "string",
                        "description": "Path to the .p8 or .p8.png cart file"
                    }
                },
                "required": ["cart_path"]
            }
        ),
        Tool(
            name="search_code",
            description="Search for code patterns across one or more PICO-8 carts",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text pattern or regex to search for"
                    },
                    "path": {
                        "type": "string",
                        "description": "Cart file or directory to search in"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "Whether pattern is a regex (default: false)"
                    }
                },
                "required": ["pattern", "path"]
            }
        ),
        Tool(
            name="compare_carts",
            description="Compare two cart versions and show code differences",
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_path_a": {
                        "type": "string",
                        "description": "Path to first cart"
                    },
                    "cart_path_b": {
                        "type": "string",
                        "description": "Path to second cart"
                    }
                },
                "required": ["cart_path_a", "cart_path_b"]
            }
        ),
        Tool(
            name="list_carts",
            description="List all PICO-8 carts in a directory with metadata",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to search for .p8 files"
                    }
                },
                "required": ["directory"]
            }
        ),
        Tool(
            name="add_documentation",
            description="Fetch and save PICO-8 documentation from a URL to make it available as a resource",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch documentation from"
                    },
                    "name": {
                        "type": "string",
                        "description": "Name for the documentation (will be saved as docs/{name}.txt)"
                    }
                },
                "required": ["url", "name"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""

    if name == "count_tokens":
        cart_path = arguments["cart_path"]

        # Validate file exists
        if not os.path.exists(cart_path):
            return [TextContent(
                type="text",
                text=f"Error: Cart file not found: {cart_path}"
            )]

        try:
            # Read the cart
            cart = read_cart(cart_path)

            # Create Source object and tokenize
            source = Source(cart.code, cart_path)
            tokens, errors = tokenize(source)
            count = count_tokens(tokens)

            # Calculate character count
            char_count = len(cart.code)

            # Calculate compressed size using shrinko8's method
            class SizeHandler:
                def __init__(self):
                    self.size = 0
                def __call__(self, prefix, name, size, limit):
                    self.size = size

            size_handler = SizeHandler()
            write_compressed_size(cart, handler=size_handler)
            compressed = size_handler.size

            # Format output similar to shrinko8
            result = f"""Token Count Results:
tokens: {count} ({count/8192*100:.2f}%)
chars: {char_count} ({char_count/65535*100:.0f}%)
compressed: {compressed} ({compressed/15616*100:.2f}%)"""

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error processing cart: {str(e)}"
            )]

    elif name == "read_cart":
        cart_path = arguments["cart_path"]
        section = arguments.get("section", "all")

        if not os.path.exists(cart_path):
            return [TextContent(
                type="text",
                text=f"Error: Cart file not found: {cart_path}"
            )]

        try:
            # plain .p8 files are read as text so PICO-8 glyphs (❎ ⬆️ ...) survive intact
            if cart_path.lower().endswith(".p8"):
                code = p8tools.code_of(cart_path)
                data = p8tools.describe_data(cart_path)
            else:
                cart = read_cart(cart_path)
                code = cart.code
                data = f"gfx {len(cart.gfx)} bytes, map {len(cart.map)} bytes, sfx {len(cart.sfx)} bytes, music {len(cart.music)} bytes"

            result = ""
            if section in ("code", "all"):
                result += f"=== CODE ===\n{code}\n"
            if section in ("gfx", "map", "sfx", "music", "all"):
                result += f"=== DATA ===\n{data}\n"
            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error reading cart: {str(e)}"
            )]

    elif name == "validate_cart":
        cart_path = arguments["cart_path"]

        if not os.path.exists(cart_path):
            return [TextContent(
                type="text",
                text=f"Error: Cart file not found: {cart_path}"
            )]

        try:
            cart = read_cart(cart_path)
            source = Source(cart_path, cart.code)
            tokens, errors = tokenize(source)
            count = count_tokens(tokens)

            # Parse and lint
            ctxt = PicoContext()
            root, parse_errors = parse(source, tokens, ctxt)
            lint_mode = arguments.get("lint", "default")
            lint_opts = {}
            if lint_mode == "default":
                # PICO-8 carts use bare globals by convention - only keep what points at real mistakes
                lint_opts = {"undefined": False, "unused-global": False, "duplicate-global": False}
            lint_errors = lint_code(ctxt, root, lint_opts) if (root and lint_mode != "none") else []
            if lint_mode == "default":
                lint_errors = [e for e in lint_errors if "isn't used" not in str(e) or re.search(r"Local '(?!i'|j'|k'|_)", str(e))]

            # Get compressed size
            class SizeHandler:
                def __init__(self):
                    self.size = 0
                def __call__(self, prefix, name, size, limit):
                    self.size = size

            size_handler = SizeHandler()
            write_compressed_size(cart, handler=size_handler)
            compressed = size_handler.size

            # Build validation report
            result = f"""=== VALIDATION REPORT ===
Tokens: {count}/8192 ({count/8192*100:.2f}%)
Characters: {len(cart.code)}/65535 ({len(cart.code)/65535*100:.0f}%)
Compressed: {compressed}/15616 ({compressed/15616*100:.2f}%)

"""

            # Check limits
            issues = []
            if count > 8192:
                issues.append(f"❌ Token limit exceeded by {count - 8192} tokens")
            if len(cart.code) > 65535:
                issues.append(f"❌ Character limit exceeded by {len(cart.code) - 65535} characters")
            if compressed > 15616:
                issues.append(f"❌ Compressed size limit exceeded by {compressed - 15616} bytes")

            # Add tokenization errors
            if errors:
                issues.append(f"\n❌ Tokenization Errors ({len(errors)}):")
                for err in errors[:10]:  # Limit to first 10
                    issues.append(f"  - {err}")

            # Add parse errors
            if parse_errors:
                issues.append(f"\n❌ Parse Errors ({len(parse_errors)}):")
                for err in parse_errors[:10]:
                    issues.append(f"  - {err}")

            # Add lint errors
            if lint_errors:
                issues.append(f"\n⚠️  Lint Warnings ({len(lint_errors)}):")
                for err in lint_errors[:40]:
                    issues.append(f"  - {err}")
                if len(lint_errors) > 40:
                    issues.append(f"  ... and {len(lint_errors) - 40} more")

            if issues:
                result += "\n".join(issues)
            else:
                result += "✅ All checks passed! Cart is valid."

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error validating cart: {str(e)}"
            )]

    elif name == "minify_cart":
        cart_path = arguments["cart_path"]
        output_path = arguments["output_path"]

        if not os.path.exists(cart_path):
            return [TextContent(
                type="text",
                text=f"Error: Cart file not found: {cart_path}"
            )]

        try:
            # Read cart
            cart = read_cart(cart_path)

            # Get original stats
            source = Source(cart_path, cart.code)
            tokens_before, _ = tokenize(source)
            count_before = count_tokens(tokens_before)

            # Process/minify using shrinko8
            ctxt = PicoContext()
            cart.code = process_code(cart, ctxt)

            # Get new stats
            source_after = Source(output_path, cart.code)
            tokens_after, _ = tokenize(source_after)
            count_after = count_tokens(tokens_after)

            # Write minified cart
            write_cart(output_path, cart)

            # Report results
            saved_tokens = count_before - count_after
            result = f"""=== MINIFICATION COMPLETE ===
Tokens before: {count_before}
Tokens after: {count_after}
Tokens saved: {saved_tokens} ({saved_tokens/count_before*100:.1f}% reduction)

Minified cart saved to: {output_path}"""

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error minifying cart: {str(e)}"
            )]

    elif name == "analyze_cart":
        cart_path = arguments["cart_path"]

        if not os.path.exists(cart_path):
            return [TextContent(
                type="text",
                text=f"Error: Cart file not found: {cart_path}"
            )]

        try:
            cart = read_cart(cart_path)
            source = Source(cart_path, cart.code)
            tokens, errors = tokenize(source)
            count = count_tokens(tokens)

            # Extract functions using regex (simpler than AST traversal)
            function_pattern = re.compile(r'function\s+(\w+)\s*\((.*?)\)')
            functions = []
            for match in function_pattern.finditer(cart.code):
                func_name = match.group(1)
                params = [p.strip() for p in match.group(2).split(',') if p.strip()]
                functions.append({'name': func_name, 'params': params})

            # Extract global variables (simple heuristic)
            global_pattern = re.compile(r'^(\w+)\s*=', re.MULTILINE)
            globals_used = set()
            for match in global_pattern.finditer(cart.code):
                var_name = match.group(1)
                if var_name not in ['local', 'function']:
                    globals_used.add(var_name)

            # Build report
            result = f"""=== CODE ANALYSIS ===

Functions Defined ({len(functions)}):"""

            for func in functions[:20]:  # Limit to first 20
                params_str = ", ".join(func['params']) if func['params'] else ""
                result += f"\n  {func['name']}({params_str})"

            if len(functions) > 20:
                result += f"\n  ... and {len(functions) - 20} more"

            result += f"\n\nGlobal Variables ({len(globals_used)}):\n"
            result += "  " + ", ".join(sorted(list(globals_used)[:30]))
            if len(globals_used) > 30:
                result += f" ... and {len(globals_used) - 30} more"

            result += f"\n\nCode Metrics:"
            result += f"\n  Tokens: {count}/8192 ({count/8192*100:.1f}%)"
            result += f"\n  Lines of code: {len(cart.code.split(chr(10)))}"
            result += f"\n  Characters: {len(cart.code)}"

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error analyzing cart: {str(e)}"
            )]

    elif name == "search_code":
        pattern = arguments["pattern"]
        path = arguments["path"]
        is_regex = arguments.get("regex", False)

        try:
            # Compile pattern if regex
            if is_regex:
                compiled_pattern = re.compile(pattern)

            # Get list of files to search
            if os.path.isfile(path):
                cart_files = [path]
            elif os.path.isdir(path):
                cart_files = glob(os.path.join(path, "**/*.p8"), recursive=True)
            else:
                return [TextContent(
                    type="text",
                    text=f"Error: Path not found: {path}"
                )]

            results = []
            for cart_file in cart_files:
                try:
                    cart = read_cart(cart_file)
                    lines = cart.code.split('\n')

                    for line_num, line in enumerate(lines, 1):
                        if is_regex:
                            if compiled_pattern.search(line):
                                results.append((cart_file, line_num, line.strip()))
                        else:
                            if pattern in line:
                                results.append((cart_file, line_num, line.strip()))
                except:
                    continue

            if not results:
                return [TextContent(
                    type="text",
                    text=f"No matches found for pattern: {pattern}"
                )]

            # Format results
            result = f"=== SEARCH RESULTS ===\nPattern: {pattern}\nFound {len(results)} matches:\n\n"

            for cart_file, line_num, line in results[:50]:  # Limit to 50 results
                result += f"{os.path.basename(cart_file)}:{line_num}: {line}\n"

            if len(results) > 50:
                result += f"\n... and {len(results) - 50} more matches"

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error searching: {str(e)}"
            )]

    elif name == "compare_carts":
        cart_path_a = arguments["cart_path_a"]
        cart_path_b = arguments["cart_path_b"]

        if not os.path.exists(cart_path_a):
            return [TextContent(
                type="text",
                text=f"Error: Cart A not found: {cart_path_a}"
            )]

        if not os.path.exists(cart_path_b):
            return [TextContent(
                type="text",
                text=f"Error: Cart B not found: {cart_path_b}"
            )]

        try:
            cart_a = read_cart(cart_path_a)
            cart_b = read_cart(cart_path_b)

            # Get token counts
            source_a = Source(cart_path_a, cart_a.code)
            tokens_a, _ = tokenize(source_a)
            count_a = count_tokens(tokens_a)

            source_b = Source(cart_path_b, cart_b.code)
            tokens_b, _ = tokenize(source_b)
            count_b = count_tokens(tokens_b)

            # Generate diff
            diff = difflib.unified_diff(
                cart_a.code.splitlines(keepends=True),
                cart_b.code.splitlines(keepends=True),
                fromfile=os.path.basename(cart_path_a),
                tofile=os.path.basename(cart_path_b),
                lineterm=''
            )

            diff_text = ''.join(diff)

            result = f"""=== CART COMPARISON ===
Cart A: {os.path.basename(cart_path_a)}
  Tokens: {count_a}
  Lines: {len(cart_a.code.splitlines())}

Cart B: {os.path.basename(cart_path_b)}
  Tokens: {count_b}
  Lines: {len(cart_b.code.splitlines())}

Token difference: {count_b - count_a:+d}

=== CODE DIFF ===
{diff_text if diff_text else "No differences in code"}
"""

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error comparing carts: {str(e)}"
            )]

    elif name == "list_carts":
        directory = arguments["directory"]

        if not os.path.isdir(directory):
            return [TextContent(
                type="text",
                text=f"Error: Directory not found: {directory}"
            )]

        try:
            cart_files = glob(os.path.join(directory, "**/*.p8"), recursive=True)

            if not cart_files:
                return [TextContent(
                    type="text",
                    text=f"No .p8 files found in {directory}"
                )]

            results = []
            for cart_file in sorted(cart_files):
                try:
                    cart = read_cart(cart_file)
                    source = Source(cart_file, cart.code)
                    tokens, _ = tokenize(source)
                    count = count_tokens(tokens)

                    rel_path = os.path.relpath(cart_file, directory)
                    results.append((rel_path, count, len(cart.code)))
                except:
                    results.append((os.path.relpath(cart_file, directory), "Error", "Error"))

            # Format results
            result = f"=== CARTS IN {directory} ===\nFound {len(results)} carts:\n\n"
            result += f"{'Cart':<50} {'Tokens':<10} {'Size':>8}\n"
            result += "-" * 70 + "\n"

            for rel_path, count, size in results:
                if count == "Error":
                    result += f"{rel_path:<50} {'ERROR':<10} {'ERROR':>8}\n"
                else:
                    result += f"{rel_path:<50} {count:<10} {size:>8}\n"

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error listing carts: {str(e)}"
            )]

    elif name == "add_documentation":
        url = arguments["url"]
        name = arguments["name"]

        try:
            # Fetch documentation from URL
            with urllib.request.urlopen(url) as response:
                content = response.read().decode('utf-8')

            # Save to docs directory
            docs_dir = Path(__file__).parent / "docs"
            docs_dir.mkdir(exist_ok=True)

            doc_path = docs_dir / f"{name}.txt"
            doc_path.write_text(content)

            result = f"""Documentation saved successfully!

Name: {name}
URI: pico8://docs/{name}
Path: {doc_path}
Size: {len(content)} characters

This documentation is now available as an MCP resource.
Claude can reference it automatically when needed."""

            return [TextContent(type="text", text=result)]

        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error fetching documentation: {str(e)}"
            )]

    # ------------------------------------------------------------------ p8tools-backed tools
    def text(s):
        return [TextContent(type="text", text=s)]

    def image(png_bytes, caption):
        return [TextContent(type="text", text=caption),
                ImageContent(type="image", data=base64.b64encode(png_bytes).decode("ascii"), mimeType="image/png")]

    try:
        if name == "simulate_cart":
            a = arguments
            r = p8tools.simulate_cart(
                a["cart_path"], a["seconds"], setup_lua=a.get("setup_lua", ""),
                log_every=a.get("log_every", 30), log_lua=a.get("log_lua", ""),
                patches=a.get("patches"), call_draw=a.get("call_draw", False),
                stop_when=a.get("stop_when", ""), timeout=a.get("timeout", 120))
            head = f"=== SIMULATION ({r['seconds']}s wall clock) ==="
            if r["error"]:
                head += f"\n❌ {r['error']}"
            return text(f"{head}\n{r['output']}")

        if name == "run_headless":
            r = p8tools.run_headless(arguments["cart_path"], arguments["driver_lua"], timeout=arguments.get("timeout", 30))
            head = f"=== HEADLESS RUN ({r['seconds']}s) ==="
            if r["error"]:
                head += f"\n❌ {r['error']}"
            return text(f"{head}\n{r['output']}")

        if name == "run_cart":
            pid = p8tools.run_cart(arguments["cart_path"], arguments.get("width", 1024), arguments.get("height", 1024))
            return text(f"PICO-8 launched (pid {pid}) running {arguments['cart_path']}. Use capture_game to look at it, send_keys to play.")

        if name == "stop_cart":
            return text(p8tools.stop_cart())

        if name == "send_keys":
            r = p8tools.send_keys(arguments["keys"], arguments.get("hold_ms", 80), arguments.get("gap_ms", 120))
            return text(json.dumps(r))

        if name == "capture_game":
            out = []
            if arguments.get("keys"):
                r = p8tools.send_keys(arguments["keys"])
                out.append(TextContent(type="text", text=f"sent keys {r['sent']} (focused={r['focused']})"))
            import time as _t
            _t.sleep(arguments.get("delay_ms", 500) / 1000)
            count = max(1, min(6, arguments.get("count", 1)))
            for i in range(count):
                if i:
                    _t.sleep(arguments.get("interval_ms", 2000) / 1000)
                png, title, size = p8tools.capture_window(arguments.get("max_size", 512))
                out += image(png, f"{title} shot {i+1}/{count} ({size[0]}x{size[1]})")
            return out

        if name == "set_sprite":
            r = p8tools.set_sprite(arguments["cart_path"], arguments["index"], arguments["rows"])
            return text(f"sprite written: {json.dumps(r)}")

        if name == "render_gfx":
            png, idx = p8tools.render_gfx(arguments["cart_path"], arguments.get("sprites", "0-15"), arguments.get("scale", 8))
            return image(png, f"sprites {idx} (checkerboard = colour 0 / transparent)")

        if name == "set_sfx":
            a = arguments
            r = p8tools.set_sfx(a["cart_path"], a["index"], notes=a.get("notes", ""), speed=a.get("speed", 16),
                                wave=a.get("wave", 1), volume=a.get("volume", 5), effect=a.get("effect", 0),
                                loop_start=a.get("loop_start"), loop_end=a.get("loop_end", 0), raw_notes=a.get("raw_notes"))
            return text(f"sfx written: {json.dumps(r)}")

        if name == "set_music":
            a = arguments
            r = p8tools.set_music(a["cart_path"], a["pattern"], a["channels"], a.get("loop_start", False),
                                  a.get("loop_end", False), a.get("stop", False))
            return text(f"music written: {json.dumps(r)}")
    except Exception as e:
        return text(f"Error in {name}: {type(e).__name__}: {e}")

    raise ValueError(f"Unknown tool: {name}")

async def main():
    """Run the MCP server."""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
