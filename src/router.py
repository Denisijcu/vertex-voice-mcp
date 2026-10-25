"""
Router ligero de herramientas para Vertex-Core.

Un modelo de 4B en CPU no es fiable eligiendo herramientas por sí solo, y cada pasada extra
cuesta segundos. Así que el router decide ANTES del modelo, con reglas simples en español e
inglés:

    "¿qué tiempo hace en La Habana?"  -> weather_now("La Habana")
    "will it rain tomorrow in Madrid" -> weather_forecast("Madrid")
    "noticias de Hugging Face"        -> news_search("Hugging Face")
    "what's the news"                 -> news_headlines()
    "¿qué hora es en Tokio?"          -> current_time("Tokio")
    "¿quién fue José Martí?"          -> wiki_lookup("José Martí")

El resultado viaja a Vertex-Core como contexto [TOOL ...] y él lo cuenta con su voz.
Si ninguna regla encaja, devuelve None y el modelo contesta como siempre.

Llama a las funciones de los servidores MCP de mcp_servers/ directamente (mismo proceso):
cero arranques de subprocesos y cero latencia de protocolo. Los mismos servidores siguen
sirviendo por stdio a LM Studio o a cualquier cliente MCP.
"""
import asyncio
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass

# Local: src/router.py -> ../mcp_servers.   Space: router.py -> ./mcp_servers
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "mcp_servers"), os.path.join(os.path.dirname(_HERE), "mcp_servers")):
    if os.path.isdir(_cand):
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break

from news_server import news_headlines, news_search  # noqa: E402
from time_server import current_time, time_difference  # noqa: E402
from weather_server import weather_forecast, weather_now  # noqa: E402
from wikipedia_server import wiki_lookup  # noqa: E402

logger = logging.getLogger("VertexRouter")

TOOL_TIMEOUT_S = 8.0

_ES_WORDS = {
    "que", "de", "la", "el", "los", "las", "en", "es", "y", "un", "una", "por", "para", "con",
    "como", "quien", "donde", "hola", "tengo", "tienes", "mi", "tu", "hay", "esto", "eso", "no",
    "si", "hora", "tiempo", "hace", "clima", "noticias", "fue", "dime", "hablame", "va", "llover",
}


@dataclass
class ToolResult:
    tool: str
    args: dict
    text: str
    latency_ms: float
    ok: bool = True  # False si la herramienta falló: se ve en métricas y el modelo lo dice honestamente


def _plain(text: str) -> str:
    """minúsculas, sin tildes y sin signos: para comparar con las reglas."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def guess_lang(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"[áéíóúñ¿¡]", low):
        return "es"
    words = _plain(text).split()
    hits = sum(w in _ES_WORDS for w in words)
    return "es" if words and hits / len(words) >= 0.3 else "en"


def _clean_arg(text: str, strip_article: bool = True) -> str:
    text = re.sub(r"[¿?¡!.,;:\"“”«»]+", " ", text or "")
    if strip_article:
        text = re.sub(r"^\s*(?:el|la|los|las|the)\s+", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()


# Lugar al final de la frase: "... en La Habana", "... in New York", "... con Madrid".
# El "^.*" codicioso hace que gane la ÚLTIMA preposición: en "horas de diferencia hay con Madrid"
# el lugar es "Madrid", no "diferencia hay con Madrid".
_PLACE_TAIL = re.compile(
    r"^.*\b(?:en|in|para|for|at|de|con|with)\s+([A-Za-zÁÉÍÓÚÑáéíóúñü][\wÁÉÍÓÚÑáéíóúñü'-]*(?:\s+[\wÁÉÍÓÚÑáéíóúñü'-]+){0,3})\s*[?.!]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
# Palabras que no son lugares aunque vayan detrás de "en"/"de"
_NOT_PLACES = {"hoy", "manana", "today", "tomorrow", "la semana", "esta semana", "this week", "el fin de semana",
               "the weekend", "la tarde", "la noche", "la manana", "tonight", "now", "ahora", "mi ciudad", "aqui",
               "here", "estos dias", "these days", "el dia", "la ciudad",
               # "pronóstico de lluvia", "forecast for rain": no son ciudades
               "lluvia", "rain", "temperatura", "temperature", "clima", "weather", "tiempo"}


def _place(text: str) -> str | None:
    m = _PLACE_TAIL.search(text)
    if not m:
        return None
    place = _clean_arg(m.group(1), strip_article=False)  # "La Habana" se queda con su "La"
    # quitar coletillas temporales: "La Habana mañana" -> "La Habana"
    place = re.sub(r"\s+(?:hoy|mañana|manana|today|tomorrow|ahora|now|esta semana|this week)$", "",
                   place, flags=re.IGNORECASE).strip()
    return None if not place or _plain(place) in _NOT_PLACES else place


# ------------------------------------------------------------------ reglas
_TIME_DIFF = re.compile(r"\b(diferencia horaria|cuantas horas de diferencia|time difference|how many hours ahead)\b")
_TIME = re.compile(r"\b(que hora|what time|la hora en|the time in|hora es|que dia es hoy|que fecha|"
                   r"what day is (?:it|today)|what s the date|today s date|fecha de hoy)\b")
_FORECAST = re.compile(r"\b(pronostico|forecast|manana|tomorrow|llover|llovera|lluvia|rain|this week|"
                       r"esta semana|fin de semana|weekend)\b")
_WEATHER = re.compile(r"\b(tiempo hace|que tiempo|clima|temperatura|calor|frio|weather|temperature|"
                      r"hot|cold|llover|lluvia|lloviendo|llueve|rain|raining|rainy|pronostico|forecast|"
                      r"hace sol|soleado|nublado|sunny|cloudy|windy|viento|humedad|humidity|umbrella|paraguas|"
                      r"storm|tormenta|snow|nieve|nevando)\b")
_NEWS = re.compile(r"\b(noticias|noticia|titulares|news|headlines|que paso con|what happened (?:with|to))\b")
_NEWS_TOPIC = re.compile(
    r"\b(?:noticias|noticia|news|titulares|headlines)\s+(?:de|del|sobre|acerca de|about|on|from|en)\s+(.+)$"
    r"|\b(?:que paso con|what happened (?:with|to))\s+(.+)$",
    re.IGNORECASE,
)
_WIKI = re.compile(
    r"^\s*(?:y\s+)?(?:quien|quienes|que|who|what)\s+(?:es|era|fue|son|fueron|is|was|are|were)\s+(.+)$"
    r"|\b(?:hablame (?:de|sobre)|cuentame (?:de|sobre)|tell me about|"
    # FIX: formas de pedir una búsqueda explícita que antes no se entendían
    r"(?:puedes |podrias )?(?:busca|buscar|buscame|investiga|investigar|consulta|consultar)"
    r"(?: en)?(?: internet| la web| la red| google| wikipedia)?(?: sobre| acerca de| de| a| por)?|"
    r"que dice wikipedia (?:de|sobre)|wikipedia(?: de| sobre)?|"
    r"(?:can you |could you )?(?:search|look up|lookup|google|research)"
    r"(?: on| in)?(?: the internet| the web| online| wikipedia)?(?: for| about| on)?|"
    r"what does wikipedia say about)\s+(.+)$",
    re.IGNORECASE,
)
# "qué es eso / esto / lo que ves" no son preguntas de enciclopedia
_WIKI_SKIP = {"eso", "esto", "that", "this", "it", "you", "tu", "usted", "lo que ves", "tu nombre", "your name"}


def _wiki_topic(original: str) -> str | None:
    plain = _plain(original)
    m = _WIKI.search(plain)
    if not m:
        return None
    # Recuperar el tema del texto ORIGINAL (con tildes y mayúsculas) por posición aproximada:
    raw_topic = (m.group(1) or m.group(2) or "").strip()
    n_words = len(raw_topic.split())
    original_words = _clean_arg(original).split()
    topic = " ".join(original_words[-n_words:]) if n_words and len(original_words) >= n_words else raw_topic
    topic = _clean_arg(topic)
    # quitar restos de la petición: "sobre José Martí" -> "José Martí"
    topic = re.sub(r"^(?:sobre|acerca de|de|del|about|for|on)\s+", "", topic, flags=re.IGNORECASE).strip()
    topic = _clean_arg(topic)
    if not topic or _plain(topic) in _WIKI_SKIP or len(topic) < 2:
        return None
    return topic


def plan(text: str) -> tuple[str, dict] | None:
    """Decide qué herramienta usar (sin ejecutarla). Devuelve (nombre, args) o None."""
    if not text or not text.strip():
        return None
    p = _plain(text)
    lang = guess_lang(text)

    if _TIME_DIFF.search(p):
        place = _place(text)
        if place:
            return "time_difference", {"place_a": place, "place_b": "Miami", "lang": lang}
    if _TIME.search(p):
        return "current_time", {"place": _place(text) or "Miami", "lang": lang}

    if _WEATHER.search(p):
        place = _place(text) or "Miami"
        if _FORECAST.search(p):
            return "weather_forecast", {"place": place, "days": 3, "lang": lang}
        return "weather_now", {"place": place, "lang": lang}

    if _NEWS.search(p):
        m = _NEWS_TOPIC.search(text)
        topic = _clean_arg(m.group(1) or m.group(2)) if m else ""
        if topic and _plain(topic) not in {"hoy", "today", "del dia", "de hoy", "ahora"}:
            return "news_search", {"query": topic, "lang": lang, "limit": 3}
        return "news_headlines", {"lang": lang, "limit": 3}

    topic = _wiki_topic(text)
    if topic:
        return "wiki_lookup", {"query": topic, "lang": lang}
    return None


_TOOLS = {
    "current_time": current_time,
    "time_difference": time_difference,
    "weather_now": weather_now,
    "weather_forecast": weather_forecast,
    "news_headlines": news_headlines,
    "news_search": news_search,
    "wiki_lookup": wiki_lookup,
}


def _for_voice(text: str) -> str:
    """Quita URLs y la línea 'Fuente:' — nadie quiere oír un link."""
    text = re.sub(r"(?im)^\s*(?:fuente|source)\s*:.*$", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return text.strip()


async def route(text: str) -> ToolResult | None:
    """Planea y ejecuta la herramienta. None si no aplica o si falla (el modelo contesta solo)."""
    decision = plan(text)
    if decision is None:
        return None
    name, args = decision
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(_TOOLS[name](**args), timeout=TOOL_TIMEOUT_S)
        # Las herramientas devuelven sus errores como texto ("Error consultando...", "No pude...")
        ok = not re.match(r"^\s*(?:error|no pude|i couldn't)", result or "", re.IGNORECASE)
    except Exception as e:
        # FIX: antes se devolvía None en silencio y el modelo decía "no tengo internet".
        # Ahora el fallo viaja como dato: se ve en métricas y el modelo lo cuenta tal cual.
        logger.warning(f"Herramienta {name}({args}) falló: {type(e).__name__}: {e}")
        result, ok = f"The {name} lookup failed right now ({type(e).__name__}).", False
    latency = (time.perf_counter() - t0) * 1000
    logger.info(f"🛠️  {name}({args}) en {latency:.0f} ms {'OK' if ok else 'FALLÓ'}")
    return ToolResult(tool=name, args=args, text=_for_voice(result), latency_ms=latency, ok=ok)
