"""
vertex-weather — servidor MCP del clima para Vertex-Core.

Herramientas:
  - weather_now(place, lang)              -> tiempo actual en una ciudad
  - weather_forecast(place, days, lang)   -> pronóstico de 1 a 5 días

Fuente: Open-Meteo (geocoding + forecast), gratis y sin API key.
Unidades automáticas: °F y mph para ciudades de EE. UU., °C y km/h para el resto.

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\weather_server.py
"""
from datetime import date

import httpx

from _common import http_client, make_server, norm_lang, t

mcp = make_server("vertex-weather")

DEFAULT_PLACE = "Miami"
IMPERIAL_COUNTRIES = {"US", "PR", "LR", "MM"}

# Códigos WMO de Open-Meteo -> descripción hablada
WMO = {
    0: ("despejado", "clear sky"), 1: ("mayormente despejado", "mainly clear"),
    2: ("parcialmente nublado", "partly cloudy"), 3: ("nublado", "overcast"),
    45: ("con niebla", "foggy"), 48: ("con niebla helada", "freezing fog"),
    51: ("con llovizna ligera", "light drizzle"), 53: ("con llovizna", "drizzle"),
    55: ("con llovizna intensa", "dense drizzle"), 61: ("con lluvia ligera", "light rain"),
    63: ("con lluvia", "rain"), 65: ("con lluvia fuerte", "heavy rain"),
    66: ("con lluvia helada", "freezing rain"), 67: ("con lluvia helada fuerte", "heavy freezing rain"),
    71: ("con nieve ligera", "light snow"), 73: ("con nieve", "snow"), 75: ("con nevada fuerte", "heavy snow"),
    77: ("con granizo fino", "snow grains"), 80: ("con chubascos ligeros", "light showers"),
    81: ("con chubascos", "showers"), 82: ("con chubascos fuertes", "violent showers"),
    85: ("con chubascos de nieve", "snow showers"), 86: ("con chubascos de nieve fuertes", "heavy snow showers"),
    95: ("con tormenta", "thunderstorm"), 96: ("con tormenta y granizo", "thunderstorm with hail"),
    99: ("con tormenta y granizo fuerte", "thunderstorm with heavy hail"),
}
DAYS = {"es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def _sky(code: int | None, lang: str) -> str:
    es, en = WMO.get(int(code or 0), ("variable", "variable"))
    return es if lang == "es" else en


async def _geocode(client: httpx.AsyncClient, place: str, lang: str) -> dict | None:
    resp = await client.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": place, "count": 1, "language": lang, "format": "json"})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    return results[0] if results else None


async def _forecast(client: httpx.AsyncClient, loc: dict, days: int, imperial: bool) -> dict:
    params = {
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": days,
    }
    if imperial:
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
    resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
    resp.raise_for_status()
    return resp.json()


def _place_name(loc: dict) -> str:
    parts = [loc.get("name"), loc.get("admin1"), loc.get("country")]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out[:2])


async def _lookup(place: str, days: int, lang: str):
    async with http_client() as client:
        loc = await _geocode(client, place, lang)
        if not loc:
            return None, None, None
        imperial = (loc.get("country_code") or "").upper() in IMPERIAL_COUNTRIES
        return loc, imperial, await _forecast(client, loc, days, imperial)


@mcp.tool()
async def weather_now(place: str = DEFAULT_PLACE, lang: str = "es") -> str:
    """Current weather in a city: sky, temperature, feels-like, humidity and wind.
    Use it for "qué tiempo hace", "how's the weather in Madrid", "¿hace calor?".
    place: city name (defaults to Miami). lang: "es" or "en"."""
    lang = norm_lang(lang)
    try:
        loc, imperial, data = await _lookup(place or DEFAULT_PLACE, 1, lang)
    except httpx.HTTPError as e:
        return t(lang, f"No pude consultar el clima ({type(e).__name__}).",
                 f"I couldn't get the weather ({type(e).__name__}).")
    if not loc:
        return t(lang, f"No encontré la ciudad «{place}».", f"I couldn't find the city '{place}'.")
    c = data["current"]
    deg, wind = ("°F", "mph") if imperial else ("°C", "km/h")
    name = _place_name(loc)
    temp, feels = round(c["temperature_2m"]), round(c["apparent_temperature"])
    sky = _sky(c.get("weather_code"), lang)
    hum, ws = round(c["relative_humidity_2m"]), round(c["wind_speed_10m"])
    return t(lang,
             f"En {name} está {sky}, {temp}{deg} (sensación de {feels}{deg}), humedad {hum}% y viento de {ws} {wind}.",
             f"In {name}: {sky}, {temp}{deg} (feels like {feels}{deg}), {hum}% humidity and {ws} {wind} wind.")


@mcp.tool()
async def weather_forecast(place: str = DEFAULT_PLACE, days: int = 3, lang: str = "es") -> str:
    """Weather forecast for the next 1-5 days: sky, max/min temperature and chance of rain.
    Use it for "¿va a llover mañana?", "forecast for this weekend", "pronóstico de 3 días"."""
    lang = norm_lang(lang)
    days = max(1, min(int(days), 5))
    try:
        loc, imperial, data = await _lookup(place or DEFAULT_PLACE, days, lang)
    except httpx.HTTPError as e:
        return t(lang, f"No pude consultar el pronóstico ({type(e).__name__}).",
                 f"I couldn't get the forecast ({type(e).__name__}).")
    if not loc:
        return t(lang, f"No encontré la ciudad «{place}».", f"I couldn't find the city '{place}'.")
    d = data["daily"]
    deg = "°F" if imperial else "°C"
    lines = [t(lang, f"Pronóstico para {_place_name(loc)}:", f"Forecast for {_place_name(loc)}:")]
    for i, day_str in enumerate(d["time"]):
        if i == 0:
            label = t(lang, "Hoy", "Today")
        elif i == 1:
            label = t(lang, "Mañana", "Tomorrow")
        else:
            label = DAYS[lang][date.fromisoformat(day_str).weekday()].capitalize()
        rain = d.get("precipitation_probability_max", [None] * len(d["time"]))[i]
        rain_txt = t(lang, f", {rain}% de lluvia", f", {rain}% chance of rain") if rain is not None else ""
        lines.append(f"- {label}: {_sky(d['weather_code'][i], lang)}, "
                     f"{round(d['temperature_2m_max'][i])}{deg} / {round(d['temperature_2m_min'][i])}{deg}{rain_txt}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()  # stdio
