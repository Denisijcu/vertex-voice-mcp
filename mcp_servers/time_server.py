"""
vertex-time — servidor MCP de hora y fecha para Vertex-Core.

Herramientas:
  - current_time(place, lang)      -> hora y fecha en una ciudad o zona horaria
  - time_difference(place_a, place_b, lang) -> cuántas horas de diferencia hay entre dos lugares

Sin red: usa el reloj del sistema y zoneinfo. En Windows necesita el paquete `tzdata`.
Acepta nombres de ciudad comunes ("Miami", "La Habana", "Madrid") o zonas IANA ("Europe/Paris").

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\time_server.py
"""
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from _common import make_server, norm_lang, t

mcp = make_server("vertex-time")

DEFAULT_PLACE = "Miami"

# Ciudades frecuentes -> zona IANA (claves sin tildes y en minúsculas)
CITIES = {
    "miami": "America/New_York", "new york": "America/New_York", "nueva york": "America/New_York",
    "washington": "America/New_York", "orlando": "America/New_York", "tampa": "America/New_York",
    "chicago": "America/Chicago", "houston": "America/Chicago", "dallas": "America/Chicago",
    "denver": "America/Denver", "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "la habana": "America/Havana", "habana": "America/Havana", "havana": "America/Havana", "cuba": "America/Havana",
    "mexico": "America/Mexico_City", "ciudad de mexico": "America/Mexico_City", "cdmx": "America/Mexico_City",
    "bogota": "America/Bogota", "lima": "America/Lima", "caracas": "America/Caracas",
    "santiago": "America/Santiago", "buenos aires": "America/Argentina/Buenos_Aires",
    "santo domingo": "America/Santo_Domingo", "san juan": "America/Puerto_Rico", "puerto rico": "America/Puerto_Rico",
    "panama": "America/Panama", "sao paulo": "America/Sao_Paulo", "toronto": "America/Toronto",
    "madrid": "Europe/Madrid", "espana": "Europe/Madrid", "barcelona": "Europe/Madrid",
    "londres": "Europe/London", "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "roma": "Europe/Rome", "rome": "Europe/Rome", "lisboa": "Europe/Lisbon", "moscu": "Europe/Moscow",
    "moscow": "Europe/Moscow", "dubai": "Asia/Dubai", "tokio": "Asia/Tokyo", "tokyo": "Asia/Tokyo",
    "pekin": "Asia/Shanghai", "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "seul": "Asia/Seoul", "seoul": "Asia/Seoul", "nueva delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
    "sidney": "Australia/Sydney", "sydney": "Australia/Sydney", "utc": "UTC",
}

DAYS = {"es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
MONTHS = {"es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                 "septiembre", "octubre", "noviembre", "diciembre"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December"]}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace(",", " ").split())


def _zone(place: str) -> tuple[ZoneInfo, str]:
    """Ciudad o zona IANA -> (ZoneInfo, nombre a mostrar). Lanza ZoneInfoNotFoundError si no existe."""
    place = (place or DEFAULT_PLACE).strip()
    key = _plain(place)
    if key in CITIES:
        return ZoneInfo(CITIES[key]), place
    return ZoneInfo(place), place  # p. ej. "Europe/Paris"


def _spoken(dt: datetime, lang: str) -> str:
    day, month = DAYS[lang][dt.weekday()], MONTHS[lang][dt.month - 1]
    if lang == "es":
        hour12 = dt.hour % 12 or 12
        part = "de la mañana" if dt.hour < 12 else ("de la tarde" if dt.hour < 19 else "de la noche")
        return f"{hour12}:{dt.minute:02d} {part}, {day} {dt.day} de {month} de {dt.year}"
    return f"{dt.strftime('%I:%M %p').lstrip('0')}, {day}, {month} {dt.day}, {dt.year}"


def _tzdata_missing() -> bool:
    """True solo si no hay base de datos de zonas (Windows sin el paquete tzdata)."""
    try:
        ZoneInfo("America/New_York")
        return False
    except ZoneInfoNotFoundError:
        return True


def _tz_error(lang: str, place: str, e: Exception) -> str:
    # FIX: antes la pista de tzdata salía con cualquier lugar desconocido, aunque tzdata estuviera instalado
    if _tzdata_missing():
        hint = t(lang, " (en Windows instala el paquete tzdata)", " (on Windows install the tzdata package)")
    else:
        hint = ""
    return t(lang, f"No conozco la zona horaria de «{place}»{hint}.",
             f"I don't know the time zone for '{place}'{hint}.")


@mcp.tool()
async def current_time(place: str = DEFAULT_PLACE, lang: str = "es") -> str:
    """Get the current local time and date in a city or IANA time zone.
    Use it for "what time is it", "qué hora es en Madrid", "what's today's date".
    place: a city ("Miami", "La Habana", "Tokyo") or an IANA zone ("Europe/Paris"). Defaults to Miami.
    lang: "es" or "en"."""
    lang = norm_lang(lang)
    try:
        tz, name = _zone(place)
    except (ZoneInfoNotFoundError, ValueError) as e:
        return _tz_error(lang, place, e)
    now = datetime.now(tz)
    return t(lang, f"En {name} son las {_spoken(now, lang)}.", f"In {name} it is {_spoken(now, lang)}.")


@mcp.tool()
async def time_difference(place_a: str, place_b: str = DEFAULT_PLACE, lang: str = "es") -> str:
    """How many hours ahead or behind one place is compared to another.
    Use it for "cuántas horas de diferencia hay con Madrid", "time difference between Tokyo and Miami"."""
    lang = norm_lang(lang)
    try:
        tz_a, name_a = _zone(place_a)
        tz_b, name_b = _zone(place_b)
    except (ZoneInfoNotFoundError, ValueError) as e:
        return _tz_error(lang, f"{place_a} / {place_b}", e)
    now = datetime.now(tz_a)
    diff_h = (now.utcoffset() - now.astimezone(tz_b).utcoffset()).total_seconds() / 3600
    hours = f"{abs(diff_h):g}"
    if diff_h == 0:
        return t(lang, f"{name_a} y {name_b} tienen la misma hora.", f"{name_a} and {name_b} have the same time.")
    if diff_h > 0:
        return t(lang, f"{name_a} va {hours} h por delante de {name_b}.", f"{name_a} is {hours} h ahead of {name_b}.")
    return t(lang, f"{name_a} va {hours} h por detrás de {name_b}.", f"{name_a} is {hours} h behind {name_b}.")


if __name__ == "__main__":
    mcp.run()  # stdio
