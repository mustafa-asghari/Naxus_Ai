#!/usr/bin/env python3
"""
Nexus - AI-Powered macOS Voice Assistant

This is the main entry point that initializes all components
and delegates to the NexusOrchestrator for the conversation loop.

Usage:
    python nexus.py

Environment Variables:
    OPENAI_API_KEY      - Required for LLM operations
    PORCUPINE_ACCESS_KEY - Required for wake word detection
    PG_DSN              - PostgreSQL connection string
    CH_HOST             - ClickHouse host (default: localhost)
    NEXUS_SESSION_ID    - Session ID (default: "default")
    NEXUS_LOG_LEVEL     - Logging level (default: INFO)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import shutil
from pathlib import Path

from dotenv import load_dotenv

from core.orchestrator import NexusOrchestrator
from data.MCP.mcp_client import MCPMemoryClient
from data.MCP.apple_mcp_client import AppleMCPClient


def configure_logging() -> logging.Logger:
    """Configure logging for Nexus."""
    level = getattr(logging, os.getenv("NEXUS_LOG_LEVEL", "INFO").upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("primp").setLevel(logging.WARNING)
    
    return logging.getLogger("nexus")


def resolve_apple_mcp_command(base_dir: Path) -> list[str]:
    """
    Resolve how to run apple-mcp.
    Prefer bun (dev mode). If bun isn't available, fall back to node + built dist.
    """
    apple_mcp_dir = base_dir / "apple_mcp" / "apple-mcp"
    apple_mcp_dist = apple_mcp_dir / "dist" / "index.js"
    
    # Check multiple possible bun locations
    bun_bin = os.getenv("NEXUS_APPLE_MCP_BIN")
    if not bun_bin:
        bun_bin = shutil.which("bun")
    if not bun_bin:
        # Check common installation locations
        home_bun = Path.home() / ".bun" / "bin" / "bun"
        if home_bun.exists():
            bun_bin = str(home_bun)
    
    node_bin = shutil.which("node")
    
    if bun_bin:
        return [
            bun_bin, 
            os.getenv("NEXUS_APPLE_MCP_SUBCMD", "run"), 
            os.getenv("NEXUS_APPLE_MCP_ENTRY", "index.ts")
        ]
    elif node_bin and apple_mcp_dist.exists():
        return [node_bin, str(apple_mcp_dist)]
    else:
        raise RuntimeError(
            "apple-mcp could not be started because 'bun' was not found and no built server was found at "
            f"{apple_mcp_dist}. Fix: install bun (recommended) or build apple-mcp to dist/index.js, "
            "or set NEXUS_APPLE_MCP_BIN to the full path of bun."
        )


async def main() -> int:
    """Main entry point."""
    load_dotenv()
    log = configure_logging()
    
    base_dir = Path(__file__).resolve().parent
    server_path = base_dir / "data" / "MCP" / "mcp_server.py"
    apple_mcp_dir = base_dir / "apple_mcp" / "apple-mcp"
    
    # Initialize MCP clients
    mcp = MCPMemoryClient(server_cmd=[sys.executable, str(server_path)])
    apple_cmd = resolve_apple_mcp_command(base_dir)
    apple = AppleMCPClient(server_cmd=apple_cmd, cwd=str(apple_mcp_dir))
    
    # Start MCP servers
    await mcp.start()
    await mcp.init_schemas()
    await apple.start()
    
    # Initialize voice system for faster response
    from skills.voice import init_voice
    init_voice()
    
    session_id = os.getenv("NEXUS_SESSION_ID", "default")
    
    try:
        # Create and run the orchestrator
        orchestrator = NexusOrchestrator(
            mcp=mcp,
            apple=apple,
            session_id=session_id,
            log=log
        )
        return await orchestrator.run()
    finally:
        await apple.stop()
        await mcp.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass