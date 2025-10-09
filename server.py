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
from mcp.types import Tool, TextContent, Resource, ResourceTemplate
import mcp.server.stdio

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
            description="Validate a PICO-8 cart: check token count, lint for errors, and report issues",
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
            cart = read_cart(cart_path)

            if section == "code" or section == "all":
                result = f"=== CODE ===\n{cart.code}\n"
                if section == "code":
                    return [TextContent(type="text", text=result)]

            if section == "gfx" or section == "all":
                gfx_info = f"=== GRAPHICS ===\nSprite data: {len(cart.gfx)} bytes\n"
                if section == "all":
                    result += gfx_info
                else:
                    return [TextContent(type="text", text=gfx_info)]

            if section == "map" or section == "all":
                map_info = f"=== MAP ===\nMap data: {len(cart.map)} bytes\n"
                if section == "all":
                    result += map_info
                else:
                    return [TextContent(type="text", text=map_info)]

            if section == "sfx" or section == "all":
                sfx_info = f"=== SFX ===\nSound effects: {len(cart.sfx)} bytes\n"
                if section == "all":
                    result += sfx_info
                else:
                    return [TextContent(type="text", text=sfx_info)]

            if section == "music" or section == "all":
                music_info = f"=== MUSIC ===\nMusic data: {len(cart.music)} bytes\n"
                if section == "all":
                    result += music_info
                else:
                    return [TextContent(type="text", text=music_info)]

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
            lint_errors = lint_code(ctxt, root, {}) if root else []

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
                for err in lint_errors[:10]:
                    issues.append(f"  - {err}")

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
