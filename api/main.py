"""
api/main.py
Instancia principal de FastAPI.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data.db import init_db
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restringir en producción
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
