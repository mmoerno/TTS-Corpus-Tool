"""
api/main.py
Instancia principal de FastAPI.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data.db import init_db

load_dotenv(Path(__file__).parent.parent / ".env")
from api.routes.usuarios import router as router_usuarios
from api.routes.municipios import router as municipios_router
from api.routes.clips import router as clips_router
from api.routes.transcripciones import router as transcripciones_router
from api.routes.splits import router as splits_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="API Corpus TTS Andaluz",
    description="Backend para la gestión del dataset TTS multiaccento andaluz.",
    version="0.1.0",
    lifespan=lifespan,
)

# Orígenes permitidos: por defecto la interfaz Gradio local (mismo equipo o
# mismo nodo Tailscale). Ampliable con CORS_ORIGINS="url1,url2" en .env, p.ej.
# para servir la interfaz desde otra IP de la VPN.
_default_origins = "http://127.0.0.1:7860,http://localhost:7860"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transcripciones_router)
app.include_router(splits_router)
app.include_router(router_usuarios)
app.include_router(municipios_router)
app.include_router(clips_router)


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok"}
