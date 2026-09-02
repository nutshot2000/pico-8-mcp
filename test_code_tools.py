#!/usr/bin/env python3
"""Test the new code analysis tools."""

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

# usage: python test_code_tools.py [path/to/cart.p8]   (default: examples/demo.p8)
cart_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "examples" / "demo.p8")
cart_dir = str(Path(cart_path).parent)

async def test_analyze():
    print("=== Testing analyze_cart ===")
    result = await server_module.call_tool("analyze_cart", {"cart_path": cart_path})
    print(result[0].text)
    print()

async def test_search():
    print("=== Testing search_code ===")
    result = await server_module.call_tool("search_code", {
        "pattern": "function ",
        "path": cart_path
    })
    print(result[0].text[:500] + "..." if len(result[0].text) > 500 else result[0].text)
    print()

async def test_list():
    print("=== Testing list_carts ===")
    result = await server_module.call_tool("list_carts", {"directory": cart_dir})
    print(result[0].text)
    print()

async def main():
    await test_analyze()
    await test_search()
    await test_list()

if __name__ == "__main__":
    asyncio.run(main())
