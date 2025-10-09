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

cart_path = "/Users/ebonura/Library/Application Support/pico-8/carts/horizon-glide/v0.16.p8"
cart_dir = "/Users/ebonura/Library/Application Support/pico-8/carts/horizon-glide"

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
