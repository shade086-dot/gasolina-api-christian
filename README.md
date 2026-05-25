# Gasolina Christian API

API local para consultar el endpoint oficial de precios de carburantes, filtrar tus gasolineras habituales, guardar histórico y recomendar dónde repostar según tramo.

## 1. Instalar

```bash
cd gasolina_api_christian
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

En Mac/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Ejecutar

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8787
```

## 3. Probar

```bash
curl http://127.0.0.1:8787/health
curl "http://127.0.0.1:8787/prices"
curl "http://127.0.0.1:8787/recommend?segment=forus_return"
curl "http://127.0.0.1:8787/recommend?segment=cabanillas_return"
```

## 4. Endpoints

- `/prices`: consulta fuente oficial y devuelve estaciones relevantes.
- `/recommend?segment=forus_return`: recomienda para Forus -> Anchuelo.
- `/recommend?segment=cabanillas_return`: recomienda para Cabanillas -> Anchuelo.
- `/history/{station_key}`: histórico local para detectar patrones de actualización.

## 5. Próximo paso

Para conectar esto con ChatGPT de forma cómoda, expón la API con ngrok o Cloudflare Tunnel y crea un GPT con Action apuntando a un OpenAPI schema.
