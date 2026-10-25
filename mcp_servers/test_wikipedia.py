"""
Prueba rápida del servidor MCP de Wikipedia, sin cliente MCP: llama las herramientas directo.

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\test_wikipedia.py
    .venv\\Scripts\\python.exe mcp_servers\\test_wikipedia.py "Alan Turing" en
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wikipedia_server import wiki_lookup, wiki_search, wiki_summary  # noqa: E402


async def main():
    if len(sys.argv) > 1:
        query = sys.argv[1]
        lang = sys.argv[2] if len(sys.argv) > 2 else "es"
        t0 = time.perf_counter()
        print(await wiki_lookup(query, lang))
        print(f"\n({(time.perf_counter() - t0) * 1000:.0f} ms)")
        return

    cases = [
        ("wiki_lookup", wiki_lookup("José Martí", "es")),
        ("wiki_lookup", wiki_lookup("Hugging Face", "en")),
        ("wiki_lookup (ambiguo)", wiki_lookup("Mercurio", "es")),
        ("wiki_search", wiki_search("Miami", "es", 3)),
        ("wiki_summary", wiki_summary("Python (lenguaje de programación)", "es")),
        ("wiki_summary (no existe)", wiki_summary("Esto no existe xyz123", "es")),
    ]
    for name, coro in cases:
        t0 = time.perf_counter()
        result = await coro
        print(f"=== {name} ({(time.perf_counter() - t0) * 1000:.0f} ms)\n{result}\n")


if __name__ == "__main__":
    asyncio.run(main())
