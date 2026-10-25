# Músculos de Vertex-Core: servidores MCP

Herramientas que le dan a Vertex-Core acceso al mundo real. Cada una es un servidor MCP independiente (stdio), así que sirve para el agente de voz, para LM Studio, para Vertex Agent o para Claude Desktop.

| Servidor | Herramientas | Fuente | Clave |
|---|---|---|---|
| `wikipedia_server.py` ✅ | `wiki_lookup`, `wiki_search`, `wiki_summary` | API de Wikimedia | no |
| `news_server.py` | `news_headlines`, `news_search` | RSS de Google News | no |
| `time_server.py` | `current_time`, `time_difference` | reloj del sistema + zoneinfo | no |
| `weather_server.py` | `weather_now`, `weather_forecast` | Open-Meteo | no |

`_common.py` reúne lo compartido: la compatibilidad con mcp 1.x y 2.x (`FastMCP` / `MCPServer`), el cliente HTTP con User-Agent, los logs silenciados y el idioma `es`/`en`.

## Instalación (una vez, desde `E:\vertex-voice-mcp`)
```powershell
.venv\Scripts\python.exe -m pip install "mcp[cli]>=1.2.0" tzdata
```
`tzdata` hace falta en Windows para las zonas horarias del servidor de hora.

## Probar sin cliente MCP (desde `E:\vertex-voice-mcp`)
```powershell
# Wikipedia
.venv\Scripts\python.exe mcp_servers\test_wikipedia.py

# Noticias, hora y clima (todo, o uno solo: news / time / weather)
.venv\Scripts\python.exe mcp_servers\test_muscles.py
.venv\Scripts\python.exe mcp_servers\test_muscles.py weather
```

## Conectarlos a LM Studio (`mcp.json`)
```json
{
  "mcpServers": {
    "vertex-wikipedia": {
      "command": "E:\\vertex-voice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["E:\\vertex-voice-mcp\\mcp_servers\\wikipedia_server.py"]
    },
    "vertex-news": {
      "command": "E:\\vertex-voice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["E:\\vertex-voice-mcp\\mcp_servers\\news_server.py"]
    },
    "vertex-time": {
      "command": "E:\\vertex-voice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["E:\\vertex-voice-mcp\\mcp_servers\\time_server.py"]
    },
    "vertex-weather": {
      "command": "E:\\vertex-voice-mcp\\.venv\\Scripts\\python.exe",
      "args": ["E:\\vertex-voice-mcp\\mcp_servers\\weather_server.py"]
    }
  }
}
```

## Diseño
- **Una herramienta, un paso.** Cada servidor tiene una herramienta principal que resuelve la pregunta típica en una sola llamada (`wiki_lookup`, `news_search`, `current_time`, `weather_now`). Es lo que mejor maneja un modelo de 4B en CPU.
- **Respuestas para el oído:** cortas, en frases completas y en el idioma pedido (`es`/`en`). La hora sale hablada ("3:15 de la tarde, domingo 25 de octubre de 2026") y las noticias numeradas, con medio y hace cuánto.
- **Unidades automáticas en el clima:** °F y mph en EE. UU. y Puerto Rico; °C y km/h en el resto.
- **Sin inventar:** desambiguaciones con opciones, ciudades inexistentes con un mensaje claro y errores de red sin romper nada.
- **Todo gratis y sin claves.**
