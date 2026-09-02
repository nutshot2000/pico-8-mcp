#!/usr/bin/env python3
"""Test the new MCP tools."""

import sys
import asyncio
from pathlib import Path

# Add shrinko8 to path
sys.path.insert(0, str(Path(__file__).parent / "shrinko8"))

# Import server module
import importlib.util
spec = importlib.util.spec_from_file_location("server", "server.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)

# usage: python test_tools.py [path/to/cart.p8]   (default: examples/demo.p8)
cart_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "examples" / "demo.p8")

async def test_validate():
    print("=== Testing validate_cart ===")
    result = await server_module.call_tool("validate_cart", {"cart_path": cart_path})
    print(result[0].text)
    print()

async def test_read_code():
    print("=== Testing read_cart (code only) ===")
    result = await server_module.call_tool("read_cart", {"cart_path": cart_path, "section": "code"})
    # Just print first 500 chars of code
    print(result[0].text[:500] + "...")
    print()

async def main():
    await test_validate()
    await test_read_code()

if __name__ == "__main__":
    asyncio.run(main())
