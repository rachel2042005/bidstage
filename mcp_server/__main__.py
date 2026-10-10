"""Start the own MCP server on stdio: ``python -m mcp_server``."""

from app import bootstrap

bootstrap.init()

from mcp_server.server import mcp  # noqa: E402


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
