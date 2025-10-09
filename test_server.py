#!/usr/bin/env python3
"""Test script to verify token counting works."""

import sys
from pathlib import Path

# Add shrinko8 to path
sys.path.insert(0, str(Path(__file__).parent / "shrinko8"))

from pico_cart import read_cart
from pico_tokenize import tokenize, count_tokens
from pico_process import Source
from pico_compress import write_compressed_size

cart_path = "/Users/ebonura/Library/Application Support/pico-8/carts/horizon-glide/v0.16.p8"

try:
    # Read the cart
    cart = read_cart(cart_path)

    # Create Source object and tokenize
    source = Source(cart_path, cart.code)
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

    # Format output
    print(f"Token Count Results for horizon-glide/v0.16.p8:")
    print(f"tokens: {count} ({count/8192*100:.2f}%)")
    print(f"chars: {char_count} ({char_count/65535*100:.0f}%)")
    print(f"compressed: {compressed} ({compressed/15616*100:.2f}%)")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
