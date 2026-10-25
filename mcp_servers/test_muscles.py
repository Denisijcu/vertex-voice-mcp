"""
Prueba de los músculos nuevos (noticias, hora, clima) sin cliente MCP: llama las herramientas directo.

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe mcp_servers\\test_muscles.py            # todo
    .venv\\Scripts\\python.exe mcp_servers\\test_muscles.py news       # solo noticias
    .venv\\Scripts\\python.exe mcp_servers\\test_muscles.py time       # solo hora
    .venv\\Scripts\\python.exe mcp_servers\\test_muscles.py weather    # solo clima
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from news_server import news_headlines, news_search  # noqa: E402
from time_server import current_time, time_difference  # noqa: E402
from weather_server import weather_forecast, weather_now  # noqa: E402

SUITES = {
    "news": [
        ("news_headlines es", lambda: news_headlines("es", 5)),
        ("news_search 'Hugging Face' en", lambda: news_search("Hugging Face", "en", 3)),
        ("news_search 'Cuba' es", lambda: news_search("Cuba", "es", 3)),
    ],
    "time": [
        ("current_time Miami es", lambda: current_time("Miami", "es")),
        ("current_time La Habana es", lambda: current_time("La Habana", "es")),
        ("current_time Tokyo en", lambda: current_time("Tokyo", "en")),
        ("time_difference Madrid vs Miami", lambda: time_difference("Madrid", "Miami", "es")),
        ("current_time (no existe)", lambda: current_time("Atlantida", "es")),
    ],
    "weather": [
        ("weather_now Miami es (°F)", lambda: weather_now("Miami", "es")),
        ("weather_now Madrid es (°C)", lambda: weather_now("Madrid", "es")),
        ("weather_forecast La Habana 3 días", lambda: weather_forecast("La Habana", 3, "es")),
        ("weather_now London en", lambda: weather_now("London", "en")),
        ("weather_now (no existe)", lambda: weather_now("Ciudadquenoexiste123", "es")),
    ],
}


async def main():
    chosen = sys.argv[1:] or list(SUITES)
    for suite in chosen:
        print(f"\n################ {suite.upper()} ################")
        for name, make in SUITES[suite]:
            t0 = time.perf_counter()
            try:
                result = await make()
            except Exception as e:  # una prueba rota no tumba las demás
                result = f"💥 {type(e).__name__}: {e}"
            print(f"=== {name} ({(time.perf_counter() - t0) * 1000:.0f} ms)\n{result}\n")


if __name__ == "__main__":
    asyncio.run(main())
