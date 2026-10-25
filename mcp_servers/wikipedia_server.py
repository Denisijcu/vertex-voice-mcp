"""
vertex-wikipedia — servidor MCP de Wikipedia para Vertex-Core (los "músculos" del agente de voz).

Herramientas:
  - wiki_lookup(query, lang)      -> busca y resume en una sola llamada (la ideal para un modelo pequeño)
  - wiki_search(query, lang)      -> lista de artículos candidatos
  - wiki_summary(title, lang)     -> resumen corto de un artículo concreto

Sin API key. Usa la API pública de Wikimedia, con User-Agent identificado como piden sus normas.
Respuestas cortas a propósito: van a ser leídas en voz alta.

Transporte: stdio (para LM Studio, Claude Desktop o cualquier cliente MCP).
Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\wikipedia_server.py
"""
import logging
import re
from urllib.parse import quote

import httpx

from _common import MCPServer  # compatible mcp 1.x / 2.x / sin paquete mcp

USER_AGENT = "VertexCoders-VoiceAgent/1.0 (https://huggingface.co/Denisijcu; vertex-voice-mcp)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
SUPPORTED_LANGS = {"es", "en"}
MAX_SENTENCES = 3

mcp = MCPServer("vertex-wikipedia")

# El servidor configura logging en INFO y httpx loguea cada petición: lo bajamos a WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _lang(lang: str) -> str:
    lang = (lang or "es").lower().strip()
    return lang if lang in SUPPORTED_LANGS else "es"


def _first_sentences(text: str, n: int = MAX_SENTENCES) -> str:
    """Recorta a las primeras n oraciones (para voz, menos es más)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡\"(])", text)
    return " ".join(parts[:n]).strip()


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp


async def _search(client: httpx.AsyncClient, query: str, lang: str, limit: int) -> list[dict]:
    resp = await _get(client, f"https://{lang}.wikipedia.org/w/api.php", {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": limit, "format": "json", "utf8": 1,
    })
    results = resp.json().get("query", {}).get("search", [])
    return [
        {"title": r["title"], "snippet": re.sub(r"<[^>]+>", "", r.get("snippet", ""))}
        for r in results
    ]


async def _summary(client: httpx.AsyncClient, title: str, lang: str) -> dict | None:
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}"
    resp = await client.get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return {
        "title": data.get("title", title),
        "type": data.get("type", "standard"),
        "description": data.get("description", ""),
        "extract": data.get("extract", ""),
        "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


@mcp.tool()
async def wiki_lookup(query: str, lang: str = "es") -> str:
    """Look up a topic on Wikipedia and return a short spoken-style summary (2-3 sentences).
    Use this for questions like "who is...", "what is...", "tell me about...".
    lang: "es" for Spanish, "en" for English (match the user's language)."""
    lang = _lang(lang)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            hits = await _search(client, query, lang, limit=3)
            if not hits:
                return (f"No encontré nada en Wikipedia sobre «{query}»." if lang == "es"
                        else f"I found nothing on Wikipedia about '{query}'.")
            for i, hit in enumerate(hits):
                page = await _summary(client, hit["title"], lang)
                if not page or not page["extract"]:
                    continue
                if page["type"] == "disambiguation":
                    if i == 0:
                        # FIX: el resultado principal es ambiguo ("Mercurio": planeta, elemento,
                        # dios, periódico...). Antes se saltaba al siguiente y devolvía el
                        # periódico chileno sin avisar. Ahora damos opciones para preguntar.
                        break
                    continue
                return f"{page['title']}: {_first_sentences(page['extract'])}\nFuente: {page['url']}"
            hits = await _search(client, f"{query}", lang, limit=6)
            titles = ", ".join(h["title"] for h in hits if h["title"].lower() != query.lower())
            return (f"«{query}» es ambiguo en Wikipedia. Opciones: {titles}." if lang == "es"
                    else f"'{query}' is ambiguous on Wikipedia. Options: {titles}.")
    except httpx.HTTPError as e:
        return f"Error consultando Wikipedia: {type(e).__name__}"


@mcp.tool()
async def wiki_search(query: str, lang: str = "es", limit: int = 5) -> str:
    """Search Wikipedia and return a list of matching article titles with a short snippet.
    Use it when the topic is ambiguous and you need to pick the right article."""
    lang = _lang(lang)
    limit = max(1, min(int(limit), 10))
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            hits = await _search(client, query, lang, limit)
    except httpx.HTTPError as e:
        return f"Error consultando Wikipedia: {type(e).__name__}"
    if not hits:
        return "Sin resultados." if lang == "es" else "No results."
    return "\n".join(f"- {h['title']}: {h['snippet']}" for h in hits)


@mcp.tool()
async def wiki_summary(title: str, lang: str = "es") -> str:
    """Return a short summary (2-3 sentences) of one exact Wikipedia article title."""
    lang = _lang(lang)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            page = await _summary(client, title, lang)
    except httpx.HTTPError as e:
        return f"Error consultando Wikipedia: {type(e).__name__}"
    if not page or not page["extract"]:
        return (f"No existe el artículo «{title}»." if lang == "es"
                else f"There is no article titled '{title}'.")
    if page["type"] == "disambiguation":
        return (f"«{page['title']}» es una página de desambiguación; usa wiki_search para elegir."
                if lang == "es" else f"'{page['title']}' is a disambiguation page; use wiki_search.")
    return f"{page['title']}: {_first_sentences(page['extract'])}\nFuente: {page['url']}"


if __name__ == "__main__":
    mcp.run()  # stdio
