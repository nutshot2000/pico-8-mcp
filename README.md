# PICO-8 MCP Server

An MCP server for working with PICO-8 game carts, built on top of [shrinko8](https://github.com/thisismypassport/shrinko8).

## Features

### Code Analysis Tools
- **count_tokens** - Count tokens, characters, and compressed size
- **analyze_cart** - List functions, globals, and code metrics
- **validate_cart** - Validate cart with token limits and linting
- **search_code** - Search for code patterns across carts
- **compare_carts** - Compare two cart versions with diff

### Cart Manipulation
- **read_cart** - Read cart sections (code, gfx, map, sfx, music)
- **minify_cart** - Minify carts to reduce token count
- **list_carts** - List all carts in a directory with metadata

### Documentation Resources
- **add_documentation** - Fetch and save PICO-8 documentation from URLs
- **Resources** - Saved documentation is available as MCP resources (e.g., `pico8://docs/api`)

## Installation

### Prerequisites

- Python 3.8+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/pico8-mcp-server.git
cd pico8-mcp-server
```

2. Initialize the shrinko8 submodule:
```bash
git submodule update --init --recursive
```

3. Install dependencies:
```bash
uv sync
```

### For Claude Code (VSCode)

Add the server using the Claude CLI:

```bash
cd /Users/ebonura/Desktop/repos/pico8-mcp-server
claude mcp add pico8 --scope project -- uv run server.py
```

Or manually create a `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "pico8": {
      "command": "uv",
      "args": ["run", "/Users/ebonura/Desktop/repos/pico8-mcp-server/server.py"]
    }
  }
}
```

### For Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "pico8": {
      "command": "uv",
      "args": ["--directory", "/Users/ebonura/Desktop/repos/pico8-mcp-server", "run", "server.py"]
    }
  }
}
```

## Usage Examples

Once configured, you can ask Claude:

- "Count the tokens in my horizon-glide cart"
- "Analyze the code structure of v0.16.p8"
- "Search for all uses of 'terrain' in my carts"
- "Compare v0.15.p8 and v0.16.p8"
- "List all carts in the horizon-glide directory"
- "Validate my cart and show any errors"

### Adding Documentation

You can fetch and save PICO-8 documentation:

```
"Fetch the PICO-8 API reference from https://pico-8.fandom.com/wiki/APIReference
and save it as 'api'"
```

Once saved, Claude can automatically reference the documentation when helping with PICO-8 code.

## License

MIT (inherits from shrinko8)
