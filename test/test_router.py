"""
Prueba del router de músculos SIN servidor, micrófono ni LM Studio.

1) Planificación: ¿qué herramienta elige para cada frase? (sin red, instantáneo)
2) Ejecución:     llama de verdad a la herramienta y muestra lo que recibiría Vertex-Core.

Uso (desde E:\\vertex-voice-mcp):
    .venv\\Scripts\\python.exe test\\test_router.py              # solo planificación
    .venv\\Scripts\\python.exe test\\test_router.py --run        # planificación + ejecución
    .venv\\Scripts\\python.exe test\\test_router.py "¿Qué tiempo hace en Madrid?"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.router import plan, route  # noqa: E402

# (frase, herramienta esperada o None)
CASES = [
    ("¿Qué tiempo hace en La Habana?", "weather_now"),
    ("What's the weather like in New York?", "weather_now"),
    ("¿Va a llover mañana en Madrid?", "weather_forecast"),
    ("Will it rain this weekend?", "weather_forecast"),
    ("¿Cuál es el pronóstico de lluvia?", "weather_forecast"),
    ("Is it raining right now?", "weather_now"),
    ("¿Está lloviendo en Miami?", "weather_now"),
    ("Do I need an umbrella today?", "weather_now"),
    ("¿Qué hora es en Tokio?", "current_time"),
    ("What time is it?", "current_time"),
    ("¿Qué día es hoy?", "current_time"),
    ("¿Cuántas horas de diferencia hay con Madrid?", "time_difference"),
    ("¿Qué noticias hay hoy?", "news_headlines"),
    ("Noticias de Hugging Face", "news_search"),
    ("What happened with SpaceX?", "news_search"),
    ("¿Quién fue José Martí?", "wiki_lookup"),
    ("Who is Alan Turing?", "wiki_lookup"),
    ("Háblame de la Revolución Industrial", "wiki_lookup"),
    ("¿Qué es la fotosíntesis?", "wiki_lookup"),
    ("Busca en Wikipedia sobre José Martí", "wiki_lookup"),
    ("Lee en Wikipedia sobre la Revolución Francesa", "wiki_lookup"),
    ("¿Puedes buscar en internet a Celia Cruz?", "wiki_lookup"),
    ("Look up Nikola Tesla", "wiki_lookup"),
    ("Can you search the web for Marie Curie?", "wiki_lookup"),
    # Estas NO deben usar herramientas: las contesta el modelo solo
    ("Hola, ¿quién eres?", None),
    ("Who are you?", None),
    ("¿Qué es eso?", None),
    ("Cuéntame un chiste", None),
    ("Are you there?", None),
    ("I read a book yesterday", None),
]


async def main():
    args = sys.argv[1:]
    run = "--run" in args
    custom = [a for a in args if a != "--run"]

    if custom:
        text = " ".join(custom)
        print(f"Plan: {plan(text)}")
        result = await route(text)
        print(f"\n{result.text if result else '(sin herramienta: contesta el modelo)'}")
        return

    ok = 0
    for text, expected in CASES:
        decision = plan(text)
        got = decision[0] if decision else None
        mark = "✅" if got == expected else "❌"
        ok += got == expected
        print(f"{mark} {text!r:52} -> {decision}")
    print(f"\n{ok}/{len(CASES)} correctas")

    if run:
        print("\n################ EJECUCIÓN ################")
        for text, expected in CASES:
            if expected is None:
                continue
            result = await route(text)
            if result:
                print(f"\n=== {text}  [{result.tool}, {result.latency_ms:.0f} ms]\n{result.text}")
            else:
                print(f"\n=== {text}  [sin resultado]")


if __name__ == "__main__":
    asyncio.run(main())
