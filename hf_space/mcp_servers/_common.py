"""Piezas compartidas por los servidores MCP de Vertex-Core."""
import logging

import httpx

# mcp 2.x renombró FastMCP -> MCPServer (mismo @tool() y run()). Compatible con ambas versiones.
# Sin el paquete mcp (p. ej. en el Space, donde el router llama las funciones directo),
# se usa un stub: @tool() deja la función intacta y solo run() exige el paquete.
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        class MCPServer:  # type: ignore[no-redef]
            def __init__(self, name: str):
                self.name = name

            def tool(self, *args, **kwargs):
                return lambda fn: fn

            def run(self, *args, **kwargs):
                raise RuntimeError("Para servir por MCP instala el paquete: pip install mcp")

USER_AGENT = "VertexCoders-VoiceAgent/1.0 (https://huggingface.co/Denisijcu; vertex-voice-mcp)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
SUPPORTED_LANGS = {"es", "en"}


def make_server(name: str) -> MCPServer:
    server = MCPServer(name)
    # El servidor configura logging en INFO y httpx loguea cada petición: lo bajamos a WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return server


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def norm_lang(lang: str) -> str:
    lang = (lang or "es").lower().strip()
    return lang if lang in SUPPORTED_LANGS else "es"


def t(lang: str, es: str, en: str) -> str:
    """Elige el texto según el idioma."""
    return es if lang == "es" else en
