"""
vertex-news — servidor MCP de noticias para Vertex-Core.

Herramientas:
  - news_headlines(lang, limit)        -> titulares principales del momento
  - news_search(query, lang, limit)    -> noticias recientes sobre un tema

Fuente: RSS público de Google News (sin API key). Devuelve titular + medio + hace cuánto,
corto y numerado, para leerlo en voz alta.

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\news_server.py
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from _common import http_client, make_server, norm_lang, t

mcp = make_server("vertex-news")

# Edición de Google News por idioma (EE. UU. en español / en inglés)
EDITIONS = {
    "es": {"hl": "es-419", "gl": "US", "ceid": "US:es-419"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


def _ago(pub: str, lang: str) -> str:
    try:
        dt = parsedate_to_datetime(pub)
        mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    except Exception:
        return ""
    if mins < 60:
        return t(lang, f"hace {max(mins, 1)} min", f"{max(mins, 1)} min ago")
    hours = mins // 60
    if hours < 24:
        return t(lang, f"hace {hours} h", f"{hours} h ago")
    days = hours // 24
    return t(lang, f"hace {days} d", f"{days} d ago")


def _parse(xml_text: str, lang: str, limit: int) -> list[str]:
    root = ET.fromstring(xml_text)
    lines = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google pone " - Medio" al final del titular: lo quitamos si ya tenemos el medio
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        title = re.sub(r"\s+", " ", title)
        if not title:
            continue
        ago = _ago(item.findtext("pubDate") or "", lang)
        meta = ", ".join(x for x in (source, ago) if x)
        lines.append(f"{title} ({meta})" if meta else title)
        if len(lines) >= limit:
            break
    return lines


async def _fetch(url: str, params: dict) -> str:
    async with http_client() as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.text


def _format(lines: list[str], lang: str, empty_msg: str) -> str:
    if not lines:
        return empty_msg
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


@mcp.tool()
async def news_headlines(lang: str = "es", limit: int = 5) -> str:
    """Get today's top news headlines (title, outlet and how long ago).
    Use it for "what's the news", "qué noticias hay", "titulares de hoy".
    lang: "es" or "en" (match the user's language)."""
    lang = norm_lang(lang)
    limit = max(1, min(int(limit), 10))
    try:
        xml_text = await _fetch("https://news.google.com/rss", EDITIONS[lang])
        lines = _parse(xml_text, lang, limit)
    except (httpx.HTTPError, ET.ParseError) as e:
        return t(lang, f"No pude traer las noticias ({type(e).__name__}).",
                 f"I couldn't fetch the news ({type(e).__name__}).")
    return _format(lines, lang, t(lang, "No hay titulares ahora mismo.", "No headlines right now."))


@mcp.tool()
async def news_search(query: str, lang: str = "es", limit: int = 5) -> str:
    """Search recent news about a topic, person, company or place (last 7 days).
    Use it for "noticias de...", "what's new with...", "qué pasó con...".
    lang: "es" or "en" (match the user's language)."""
    lang = norm_lang(lang)
    limit = max(1, min(int(limit), 10))
    params = {"q": f"{query} when:7d", **EDITIONS[lang]}
    try:
        xml_text = await _fetch("https://news.google.com/rss/search", params)
        lines = _parse(xml_text, lang, limit)
    except (httpx.HTTPError, ET.ParseError) as e:
        return t(lang, f"No pude buscar noticias ({type(e).__name__}).",
                 f"I couldn't search the news ({type(e).__name__}).")
    return _format(lines, lang, t(lang, f"No encontré noticias recientes sobre «{query}».",
                                  f"No recent news about '{query}'."))


if __name__ == "__main__":
    mcp.run()  # stdio
