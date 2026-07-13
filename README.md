# Dataset TTS Multiaccento Andaluz

Herramienta de TFG (Trabajo de Fin de Grado) para la construcción, revisión y exportación de datasets de audio destinados al entrenamiento y fine-tuning de modelos Text-to-Speech con acentos provinciales del español andaluz.

---

## Índice

1. [Motivación y contexto](#1-motivación-y-contexto)
2. [Arquitectura general](#2-arquitectura-general)
3. [Estructura de ficheros](#3-estructura-de-ficheros)
4. [Base de datos](#4-base-de-datos)
5. [API REST (FastAPI)](#5-api-rest-fastapi)
6. [Flujo de datos completo](#6-flujo-de-datos-completo)
7. [Interfaz Gradio](#7-interfaz-gradio)
8. [Modelos TTS soportados](#8-modelos-tts-soportados)
9. [Formatos de exportación](#9-formatos-de-exportación)
10. [Autenticación y roles](#10-autenticación-y-roles)
11. [Configuración](#11-configuración)
12. [Instalación](#12-instalación)
13. [Arranque del sistema](#13-arranque-del-sistema)
14. [Convención de nombres de archivo](#14-convención-de-nombres-de-archivo)
15. [Notas de despliegue](#15-notas-de-despliegue)
16. [Problemas conocidos y soluciones](#16-problemas-conocidos-y-soluciones)

---

## 1. Motivación y contexto

El español andaluz no es un único acento sino un conjunto de variedades dialectales distribuidas por provincias y municipios. Los modelos TTS genéricos entrenados con español peninsular estándar no capturan estas variantes. El objetivo de este TFG es:

- Recopilar y catalogar audios de hablantes nativos de distintos municipios de Andalucía.
- Transcribirlos automáticamente con Whisper y permitir corrección manual colaborativa.
- Exportarlos en formatos listos para fine-tuning de múltiples modelos TTS.
- Proporcionar una interfaz web accesible para equipos distribuidos (recolectores, revisores, administradores).

La preservación dialectal es la motivación principal: el acento andaluz tiene rasgos fonéticos propios (seseo, ceceo, aspiración, pérdida de consonantes finales) que se perderían sin una documentación digital de calidad.

---

## 2. Arquitectura general

```
┌──────────────────────────────────────────────────────────┐
│                    GRADIO (puerto 7860)                   │
│   gui_tts_andaluz.py  ←→  ui/api_client.py               │
│   Tabs: Bienvenida | Procesar | Revisar | Estadísticas   │
│         Entrenar | Exportar                               │
└───────────────────────┬──────────────────────────────────┘
                        │ HTTP REST + JWT
┌───────────────────────▼──────────────────────────────────┐
│                  FASTAPI (puerto 8000)                    │
│   api/main.py                                             │
│   Rutas: /auth  /clips  /splits  /transcripciones        │
│          /municipios                                      │
└───────────────────────┬──────────────────────────────────┘
                        │ SQLAlchemy ORM
┌───────────────────────▼──────────────────────────────────┐
│              POSTGRESQL (o SQLite fallback)               │
│   Tablas: provincia, municipio, toponimo, usuario,        │
│           hablante, clip, correccion                      │
└──────────────────────────────────────────────────────────┘

Almacenamiento local
  dataset/                 ← WAVs procesados + metadata.csv por municipio
  exports/                 ← Datasets exportados por formato/modelo
  NGA_TOPONIMOS_*.csv      ← Catálogo de topónimos del NGA (IGN)
```

Los dos procesos (Gradio + FastAPI) son independientes y se lanzan por separado. La GUI se comunica con la API únicamente via HTTP usando JWT. Pueden correr en la misma máquina o en servidores distintos (ajustar `API_BASE` en `.env`).

---

## 3. Estructura de ficheros

```
Andalucia/
│
├── gui_tts_andaluz.py           # Punto de entrada Gradio
├── procesar_audios_andalucia.py # Lógica de procesado de audio + Whisper
├── config.py                    # Constantes globales (rutas, parámetros de audio)
├── start_gui.ps1                # Arranque Gradio — Windows
├── start_gui.sh                 # Arranque Gradio — Linux / Raspberry Pi
├── start_api.ps1                # Arranque API — Windows
├── start_api.sh                 # Arranque API — Linux / Raspberry Pi
├── install.ps1                  # Instalacion automatica — Windows
├── install.sh                   # Instalacion automatica — Linux / Raspberry Pi
├── requirements.txt
├── .env                         # Variables de entorno (NO subir a git)
├── .gitignore
│
├── scripts/
│   ├── db_backup.ps1            # Exportar BD a SQL — Windows
│   ├── db_backup.sh             # Exportar BD a SQL — Linux
│   └── db_restore.sh            # Restaurar BD desde SQL — Linux
│
├── api/
│   ├── main.py                  # FastAPI app, lifespan, CORS, routers
│   ├── auth.py                  # JWT: crear_token, get_current_user, roles
│   └── routes/
│       ├── clips.py             # CRUD de clips de audio
│       ├── usuarios.py          # Login, crear usuario, perfil
│       ├── splits.py            # Generación de train/eval + exportación API
│       ├── transcripciones.py   # Clips pendientes + historial de correcciones
│       └── municipios.py        # Listado de municipios/provincias
│
├── core/
│   ├── audio.py                 # Segmentación por silencio (pydub/ffmpeg)
│   ├── transcripcion.py         # Transcripción Whisper con prompt NGA
│   ├── nga.py                   # Carga del catálogo de topónimos NGA
│   └── train.py                 # Entrenadores: XTTSTrainer, PiperTrainer, F5Trainer
│
├── data/
│   ├── db.py                    # Modelos SQLAlchemy + utilidades de sesión
│   ├── export.py                # Pipeline de exportación (todos los formatos)
│   ├── csv_store.py             # Helpers para lectura/escritura de metadata.csv
│   ├── migrar.py                # Script de migración de CSV a BD
│   └── migrar_nga.py            # Importación del catálogo NGA a BD
│
├── ui/
│   ├── api_client.py            # Cliente HTTP centralizado (token global)
│   ├── tab_bienvenida.py        # Pestaña informativa / ayuda
│   ├── tab_procesar.py          # Subida, conversión, segmentación, transcripción
│   ├── tab_revisar.py           # Revisión y corrección de transcripciones
│   ├── tab_estadisticas.py      # Estadísticas por hablante + gestión de splits
│   ├── tab_entrenar.py          # Zero-shot y fine-tuning de modelos TTS
│   ├── tab_exportar.py          # Exportación de datasets por formato
│   └── tab_usuarios.py          # Gestión de usuarios (solo admin): listar, crear, desactivar
│
├── dataset/                     # Generado en tiempo de ejecución
│   └── <Provincia>/
│       └── <Municipio>_<INE>/
│           ├── wavs/            # WAVs a 22050 Hz mono 16-bit
│           ├── metadata.csv     # audio|transcripcion|idioma (Whisper original, NUNCA se modifica)
│           ├── metadata_train.csv
│           └── metadata_eval.csv
│
├── .hf_cache/                   # Caché de modelos HuggingFace (portable con el proyecto)
│   └── hub/
│       └── models--tts-hub--XTTS-v2/   # ~1.8 GB, descargado la primera vez
│
├── exports/                     # Datasets listos para entrenamiento
│   └── <nombre_exportación>/
│       ├── ljspeech/
│       ├── xtts/
│       ├── piper/
│       ├── f5/
│       ├── commonvoice/
│       └── csv/
│
└── NGA_TOPONIMOS_20260309.csv   # Fuente de topónimos (IGN/NGA)
```

---

## 4. Base de datos

### Diagrama ER simplificado

```
Provincia (1) ──── (N) Municipio (1) ──── (N) Hablante (1) ──── (N) Clip
                                │                                      │
                           (N) Toponimo                          (N) Correccion
                                                                       │
                                                                  (N) Usuario
```

### Tablas

#### `provincia`
| columna  | tipo        | descripción                 |
|----------|-------------|-----------------------------|
| id       | INTEGER PK  |                             |
| codigo   | VARCHAR(2)  | Código INE (ej: "41")       |
| nombre   | VARCHAR(50) | Nombre de la provincia      |

Provincias soportadas: Almería (04), Cádiz (11), Córdoba (14), Granada (18), Huelva (21), Jaén (23), Málaga (29), Sevilla (41).

#### `municipio`
| columna        | tipo         | descripción                               |
|----------------|--------------|-------------------------------------------|
| id             | INTEGER PK   |                                           |
| provincia_id   | FK provincia |                                           |
| codigo_ine     | VARCHAR(5)   | Código INE de 5 dígitos (ej. "41095")    |
| nombre         | VARCHAR(100) | Nombre normalizado                        |
| nombre_oficial | VARCHAR(100) | Nombre oficial completo (opcional)        |
| coordenada_x   | FLOAT        | Centroide UTM de sus topónimos (EPSG:25830, ETRS89 huso 30N) |
| coordenada_y   | FLOAT        |                                           |

#### `toponimo`
| columna      | tipo         | descripción                                      |
|--------------|--------------|--------------------------------------------------|
| id           | INTEGER PK   |                                                  |
| municipio_id | FK municipio |                                                  |
| nombre       | VARCHAR(200) | Nombre de paraje, calle, accidente geográfico... |
| estado       | VARCHAR(20)  | "normalizado" / "pendiente" / "descartado"       |

Los topónimos se usan como prompt de Whisper para mejorar el reconocimiento de nombres propios locales. Se cargan desde `NGA_TOPONIMOS_*.csv` (Nomenclátor Geográfico de Andalucía, IGN) via `data/migrar_nga.py`.

> **Nota sobre este repositorio público**: la versión original del catálogo NGA (26 937 topónimos: parajes, cortijos, arroyos, accidentes geográficos... con sus coordenadas) es un dato confidencial y no se puede publicar. El `NGA_TOPONIMOS_20260309.csv` incluido aquí es una versión reducida a información pública: los 785 municipios de las 8 provincias de Andalucía con su nombre oficial y código INE (fuente: [INE](https://www.ine.es/daco/daco42/codmun/codmun.htm)), usando el propio nombre del municipio como marcador de posición en vez de un topónimo real. Sirve para ejecutar `data/migrar_nga.py` y construir la base de datos con la tabla `municipio` completa desde el primer momento; solo se pierde el prompt de topónimos NGA real para Whisper. Si tienes acceso a un catálogo NGA completo propio, puedes sustituir el fichero por el tuyo manteniendo el mismo nombre y columnas.

#### `usuario`
| columna       | tipo         | descripción                                     |
|---------------|--------------|-------------------------------------------------|
| id            | INTEGER PK   |                                                 |
| uvus          | VARCHAR(50)  | Identificador único (UVUS de la Universidad)    |
| nombre        | VARCHAR(100) |                                                 |
| rol           | VARCHAR(20)  | `recolector` / `revisor` / `admin`              |
| password_hash | VARCHAR(255) | bcrypt hash de la contraseña                    |
| activo        | BOOLEAN      | Desactivación sin borrado físico                |
| creado_en     | DATETIME     |                                                 |
| ultimo_acceso | DATETIME     | Actualizado en cada login                       |

#### `hablante`
| columna      | tipo         | descripción                               |
|--------------|--------------|-------------------------------------------|
| id           | INTEGER PK   |                                           |
| municipio_id | FK municipio |                                           |
| usuario_id   | FK usuario   | Usuario asignado al hablante              |
| codigo       | VARCHAR(2)   | Código dentro del municipio ("01", "02")  |
| edad         | INTEGER      |                                           |
| genero       | VARCHAR(1)   | `M` / `F` / `X`                          |

Restricción: `(municipio_id, codigo)` es único.

#### `clip`
| columna        | tipo         | descripción                                               |
|----------------|--------------|-----------------------------------------------------------|
| id             | INTEGER PK   |                                                           |
| hablante_id    | FK hablante  |                                                           |
| creado_por_id  | FK usuario   |                                                           |
| nombre_archivo | VARCHAR(200) | Ruta relativa: `wavs/41095_01_seg01.wav` — única en BD   |
| transcripcion  | TEXT         | Texto vigente (Whisper inicial o corrección más reciente) |
| idioma         | VARCHAR(2)   | `es` siempre                                              |
| duracion_s     | FLOAT        | Duración en segundos                                      |
| split          | VARCHAR(5)   | `train` / `eval` / NULL (sin asignar)                    |
| activo         | BOOLEAN      | `False` = clip descartado (soft delete)                   |
| creado_en      | DATETIME     |                                                           |
| actualizado_en | DATETIME     | Se actualiza automáticamente en cada cambio               |

#### `correccion`
| columna        | tipo       | descripción                              |
|----------------|------------|------------------------------------------|
| id             | INTEGER PK |                                          |
| clip_id        | FK clip    |                                          |
| usuario_id     | FK usuario |                                          |
| texto_anterior | TEXT       | Transcripción antes de la corrección     |
| texto_nuevo    | TEXT       | Nueva transcripción                      |
| creado_en      | DATETIME   |                                          |

Cada corrección es un registro inmutable. La transcripción vigente siempre está en `clip.transcripcion`. La tabla `correccion` proporciona auditoría completa del historial de cambios.

### Índices
```
idx_municipio_provincia  → municipio.provincia_id
idx_toponimo_municipio   → toponimo.municipio_id
idx_hablante_municipio   → hablante.municipio_id
idx_clip_hablante        → clip.hablante_id
idx_clip_split           → clip.split
idx_clip_activo          → clip.activo
idx_correccion_clip      → correccion.clip_id
idx_correccion_usuario   → correccion.usuario_id
```

### Fallback SQLite

Si PostgreSQL no está disponible (desarrollo local, CI), `data/db.py` crea automáticamente `data_dev.sqlite3` en la raíz del proyecto. El esquema es idéntico. Para producción usar siempre PostgreSQL.

---

## 5. API REST (FastAPI)

La API arranca en `http://localhost:8000`. Documentación interactiva disponible en `/docs` (Swagger UI) y `/redoc`.

### Autenticación

Todos los endpoints (excepto `/health` y `POST /auth/login`) requieren:
```
Authorization: Bearer <JWT>
```

El JWT se obtiene con `POST /auth/login` usando `Content-Type: application/x-www-form-urlencoded` y campos `username` / `password`.

### Tabla de endpoints

#### Sistema
| método | ruta    | auth | descripción  |
|--------|---------|------|--------------|
| GET    | /health | No   | Health check |

#### Autenticación y usuarios — prefijo `/auth`
| método | ruta                              | rol mínimo  | descripción                        |
|--------|-----------------------------------|-------------|------------------------------------|
| POST   | /auth/login                       | —           | Obtener JWT                        |
| GET    | /auth/me                          | cualquiera  | Perfil del usuario autenticado     |
| POST   | /auth/usuarios                    | admin       | Crear nuevo usuario                |
| GET    | /auth/usuarios                    | admin       | Listar todos los usuarios          |
| PATCH  | /auth/usuarios/{uvus}/desactivar  | admin       | Desactivar usuario (soft delete)   |
| POST   | /auth/cambiar-password            | cualquiera  | Cambiar propia contraseña          |

#### Clips — prefijo `/clips`
| método | ruta          | rol mínimo  | descripción                                          |
|--------|---------------|-------------|------------------------------------------------------|
| POST   | /clips        | cualquiera  | Registrar un clip (llamado al procesar audio)        |
| GET    | /clips        | cualquiera  | Listar clips (filtros: hablante_id, split, activo)   |
| GET    | /clips/{id}   | cualquiera  | Detalle de un clip                                   |
| PUT    | /clips/{id}   | revisor     | Corregir transcripción (crea registro en correccion) |
| DELETE | /clips/{id}   | revisor     | Desactivar clip (soft delete, activo=False)          |

#### Transcripciones — prefijo `/transcripciones`
| método | ruta                               | rol mínimo | descripción                               |
|--------|------------------------------------|------------|-------------------------------------------|
| GET    | /transcripciones/pendientes        | cualquiera | Clips activos sin ninguna corrección      |
| GET    | /transcripciones/historial/{id}    | cualquiera | Historial completo de correcciones        |

#### Splits — prefijo `/splits`
| método | ruta                      | rol mínimo | descripción                                      |
|--------|---------------------------|------------|--------------------------------------------------|
| POST   | /splits/generar           | admin      | Asignar train/eval a clips sin split             |
| GET    | /splits/export/{formato}  | cualquiera | Descargar split como fichero descargable         |

Formatos de `/splits/export/{formato}`: `ljspeech`, `commonvoice`, `csv`.

#### Municipios — prefijo `/municipios`
| método | ruta        | rol mínimo | descripción       |
|--------|-------------|------------|-------------------|
| GET    | /municipios | cualquiera | Listar municipios |

### Schemas Pydantic clave

**ClipCreate** (POST /clips):
```json
{
  "nombre_archivo": "wavs/41095_01_seg01.wav",
  "transcripcion": "texto reconocido por Whisper",
  "hablante_id": 1,
  "duracion_s": 7.3,
  "split": null
}
```

**ClipUpdate** (PUT /clips/{id}):
```json
{ "transcripcion": "texto corregido manualmente" }
```

**TokenResponse** (POST /auth/login):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "rol": "revisor",
  "nombre": "María García"
}
```

Ejemplo con curl:
```bash
# Login
TOKEN=$(curl -s -X POST -d "username=admin&password=mmg" \
  http://localhost:8000/auth/login | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Listar clips
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/clips
```

---

## 6. Flujo de datos completo

### 6.1 Procesado de audio

```
Audio original (mp3, ogg, mp4, mkv, wav, etc.)
        │
        ▼ ffmpeg → convert_direct()
WAV mono 22050 Hz 16-bit s16
        │
        ▼ pydub split_on_silence (MIN_SILENCE_MS=400ms, SILENCE_THRESH=-40dBFS)
Segmentos 2–15 segundos
        │
        ▼ Whisper (modelo configurable, prompt con topónimos NGA del municipio)
Transcripción automática
        │
        ├── CSV local: dataset/<Prov>/<Mun>/metadata.csv  → audio|trans|idioma
        │              metadata_global.csv                 → + municipio + provincia
        │
        └── POST /clips → BD (clip.transcripcion = texto Whisper inicial)
```

Parámetros de segmentación (en `config.py`):

| constante        | valor  | descripción                                    |
|------------------|--------|------------------------------------------------|
| `MAX_DURATION`   | 15 s   | Duración máxima; segmentos más largos se dividen por tiempo |
| `MIN_DURATION`   | 2 s    | Duración mínima; segmentos más cortos se descartan |
| `SILENCE_THRESH` | -40 dBFS | Umbral de silencio para detectar pausas     |
| `MIN_SILENCE_MS` | 400 ms | Silencio mínimo para considerar un corte      |
| `KEEP_SILENCE_MS`| 100 ms | Padding de silencio a mantener en cada segmento |

### 6.2 Revisión de transcripciones

```
Revisor (pestaña "Revisar transcripciones")
        │
        ▼ GET /transcripciones/pendientes → clips sin corrección
Reproduce audio + muestra transcripción editable
        │
        ▼ Corrección manual del texto
        │
        ▼ PUT /clips/{id}  { "transcripcion": "texto correcto" }
BD: clip.transcripcion = nuevo texto
    + INSERT correccion (texto_anterior, texto_nuevo, usuario_id)
```

**Importante**: el CSV local (`metadata.csv`) NUNCA se actualiza con correcciones. El CSV contiene siempre la transcripción original de Whisper. La fuente de verdad para exportaciones es siempre `clip.transcripcion` en la BD.

### 6.3 Exportación

```
export_dataset(source_csv, out_base, formats, copy_mode, split, hablante_prefix)
        │
        ├── 1. Lee filas del CSV local (audio|trans|idioma)
        │
        ├── 2. _enrich_rows_from_db()
        │       • Consulta BD por nombre_archivo
        │       • Sustituye transcripción CSV → transcripción BD (corregida)
        │       • Filtra clips con activo=False
        │
        ├── 3. Filtra por hablante (si hablante_prefix)
        │
        └── 4. Para cada formato (ljspeech/xtts/piper/f5/commonvoice/csv):
                • Copia/symlink/referencia WAVs al directorio de export
                • Escribe metadata en el formato específico
```

---

## 7. Interfaz Gradio

La app Gradio corre en `http://localhost:7860` con autenticación integrada. El login de Gradio llama internamente a `POST /auth/login` y almacena el JWT en `ui/api_client.py` (variable de módulo `_token`). La variable de entorno `API_BASE` controla a qué instancia de la API apunta la GUI.

### Pestaña: Bienvenida

Documentación integrada sobre formatos, modos de copia y estructura de ficheros. Incluye además un formulario de "Cambiar mi contraseña" (llama a `POST /auth/cambiar-password`), disponible para cualquier rol ya que es la única pestaña visible para todos los usuarios independientemente de su rol.

### Pestaña: Procesar audios

1. Selección de provincia y municipio (cargados desde el catálogo NGA)
2. Selección o registro de hablante (código de 2 dígitos dentro del municipio)
3. Entrada de audio: ruta de carpeta, arrastrar archivos (cualquier formato de `AV_EXTS`) o grabación directa desde el micrófono del navegador
4. Procesado automático: conversión WAV → segmentación por silencio → Whisper
5. Lista de clips generados con transcripciones Whisper
6. Registro de cada clip en la API (POST /clips)

El prompt de Whisper se construye con topónimos del municipio seleccionado desde la BD (`data/db.py::get_whisper_prompt_municipio`), lo que mejora significativamente el reconocimiento de nombres propios locales.

### Pestaña: Revisar transcripciones

1. Carga clips pendientes (sin correcciones) desde `GET /transcripciones/pendientes`
2. Reproduce audio en el navegador
3. Muestra transcripción editable
4. Al guardar: `PUT /clips/{id}` → corrección registrada en BD
5. Navegación entre clips con botones Anterior/Siguiente
6. Opción de desactivar clips incorregibles (`DELETE /clips/{id}`)

Estado de sesión con `gr.State()` (por sesión de usuario) para evitar conflictos entre usuarios concurrentes en la misma instancia.

### Pestaña: Estadísticas

- Tabla por hablante: número de clips, duración total, estado
  - `< 10 min` → Insuficiente
  - `10–30 min` → Limitado
  - `≥ 30 min` → OK (mínimo recomendado para XTTS)
- Botón "Generar splits": llama `POST /splits/generar` (solo admin), asigna train/eval con ratio 0.85
- Botón "Vaciar _eliminados/": limpia archivos descartados durante el procesado

### Pestaña: Entrenar

Dos modos seleccionables mediante radio button:

#### Modo Zero-Shot

Usa el modelo base preentrenado sin entrenamiento adicional. Requiere un audio de referencia del hablante (≥3 s).

**Fuente de referencia**:
- *Hablante del dataset*: selector de hablante → selector de TODOS sus clips con transcripción corregida de BD → preview del audio seleccionado. Muestra nombre, duración y transcripción completa de cada clip.
- *Subir / Grabar*: carga directa de audio externo.

Modelos disponibles en zero-shot: XTTS v2, F5-TTS.

#### Modo Fine-Tuning

Pipeline en 4 pasos (acordeones):

**Paso 1 — Fuente**: qué datos exportar (global / por provincia / por municipio / por hablante), modelo, modo de ejecución (local / servidor remoto).

**Paso 2 — Configuración**: nombre del modelo, epochs, batch size, learning rate. Los rangos se actualizan automáticamente según el modelo:

| Modelo  | Epochs (default) | Rango         | Batch | LR     |
|---------|-----------------|---------------|-------|--------|
| XTTS v2 | 10              | 1 – 500       | 2     | 5e-6   |
| Piper   | 6000            | 1000 – 10000  | 16    | 1e-4   |
| F5-TTS  | 100             | 50 – 1000     | 4     | 1e-4   |

**Paso 3 — Entrenamiento**: botón de inicio, log en tiempo real (función generadora con `yield`), progreso via `gr.Progress()`. Llama internamente a `export_dataset()` y luego al entrenador correspondiente.

**Paso 4 — Probar**: se abre automáticamente al finalizar el entrenamiento (cadena `.then(_abrir_paso4_si_entrenado)`). Cuadro de texto → síntesis → reproducción de audio resultante.

Estado de sesión: `gr.State(_default_ft_state())` con campos:
- `model_code`: `"xtts"` / `"piper"` / `"f5"`
- `model_dir`: ruta al directorio con el modelo entrenado
- `onnx_path`: ruta al fichero `.onnx` (Piper únicamente)
- `trained`: `True` tras un entrenamiento exitoso

### Pestaña: Exportar

- Selección de formato(s): ljspeech, xtts, piper, f5, commonvoice, csv
- Filtros: split (train/eval/todos), por provincia, por municipio, por hablante
- Modo de copia: `copy` (recomendado en Windows), `symlink`, `reference`
- Nombre de la exportación (crea subdirectorio en `exports/`)
- Progreso de exportación en tiempo real

### Pestaña: Gestión de usuarios (solo admin)

Solo visible si el usuario que ha iniciado sesión tiene rol `admin`. La visibilidad se decide en un `app.load()` que llama a `GET /auth/me` al cargar la página y oculta la pestaña (`gr.update(visible=False)`) si el rol no es `admin`; los demás roles no llegan a verla.

- **Listado**: tabla con UVUS, nombre, rol y estado (activo/inactivo) de todos los usuarios (`GET /auth/usuarios`).
- **Crear usuario**: formulario con UVUS, nombre, rol (`recolector` / `revisor` / `admin`) y contraseña con confirmación (`POST /auth/usuarios`). Valida en cliente que las contraseñas coincidan; el servidor valida UVUS único y rol permitido.
- **Desactivar usuario**: introduce el UVUS y llama a `PATCH /auth/usuarios/{uvus}/desactivar` (*soft delete*: el usuario no puede volver a iniciar sesión, pero su historial de correcciones y clips se conserva).

**Aviso de diseño**: como el token de sesión (`ui/api_client._token`) es una variable global de módulo y no está aislada por usuario (a diferencia del `gr.State()` de la pestaña Revisar), la comprobación de rol asume que solo hay una sesión de administración activa a la vez en la misma instancia del proceso Gradio. Para un despliegue con varios administradores conectados simultáneamente habría que migrar el token a almacenamiento por sesión.

---

## 8. Modelos TTS soportados

### XTTS v2 (Coqui / Idiap fork)

**Zero-shot**: el modelo base (~1.8 GB, descarga automática desde Hugging Face la primera vez) sintetiza con cualquier audio de referencia de ≥ 3 segundos.

```python
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
tts.tts_to_file(text="Texto a sintetizar", speaker_wav="referencia.wav",
                language="es", file_path="salida.wav")
```

**Fine-tuning**: requiere datos en formato LJSpeech. Pipeline interno (`core/train.py::XTTSTrainer`):
1. `export_dataset(formats=["xtts"])` → genera `exports/<nombre>/xtts/wavs/` + `metadata_train.csv` + `metadata_eval.csv`
2. `XTTSTrainer.train_local(dataset_dir, output_dir, epochs, batch, lr, ...)`
3. Usa `BaseDatasetConfig` + `XttsConfig` + `Trainer` de la librería TTS
4. Síntesis con modelo entrenado: `XTTSTrainer.synthesize(model_dir, text, reference_audio, output_path)`

**Mínimo recomendado**: 30 minutos de audio por hablante.

**Python 3.12**: la versión oficial de CoquiTTS no es compatible. Usar el fork de Idiap:
```bash
pip install git+https://github.com/idiap/coqui-ai-TTS
pip install torch==2.7.1+cpu torchaudio==2.7.1+cpu \
    --index-url https://download.pytorch.org/whl/cpu
```

### Piper TTS (Rhasspy)

Solo fine-tuning. Arquitectura VITS, muy eficiente en CPU. Pipeline (`core/train.py::PiperTrainer`):
1. **Preprocesado**: `piper_train.preprocess` — fonemización con `espeak-ng`, genera `dataset.jsonl`
2. **Entrenamiento**: `piper_train.train` — VITS desde checkpoint base
3. **Exportación ONNX**: `PiperTrainer.export_onnx(model_dir)` → `.onnx` listo para el runtime Piper
4. **Síntesis**: `piper --model modelo.onnx --output-file salida.wav` (stdin = texto)

Formato de datos: `wavs/filename.wav|speaker_id|texto` (sin cabecera).

Requiere `espeak-ng` instalado en el sistema. **Mínimo recomendado**: 20 minutos de audio.

### F5-TTS

Zero-shot y fine-tuning. Requiere GPU (CUDA). La implementación actual es un stub que delega en la CLI de F5-TTS. Formato de datos: rutas absolutas al WAV + texto. Requiere `config.yaml` propio del repositorio F5-TTS.

---

## 9. Formatos de exportación

Todos los formatos se generan por `data/export.py`. La función `_enrich_rows_from_db()` garantiza que se usen siempre las transcripciones corregidas de la BD.

### LJSpeech (`exports/<nombre>/ljspeech/`)
```
stem|transcripcion|transcripcion
```
Sin cabecera. La columna 0 es el stem (sin extensión, sin prefijo `wavs/`). El formatter LJSpeech de CoquiTTS construye la ruta como `{root_path}/wavs/{stem}.wav`.

### XTTS (`exports/<nombre>/xtts/`)
Igual que LJSpeech más splits separados sin cabecera:
- `metadata.csv` — dataset completo
- `metadata_train.csv` — split train (85%)
- `metadata_eval.csv` — split eval (15%)

### Piper (`exports/<nombre>/piper/`)
```
wavs/filename.wav|speaker_id|transcripcion
```
`speaker_id` extraído del campo numérico del nombre de archivo: `41095_01_seg01.wav` → `speaker_id=1`.

### F5-TTS (`exports/<nombre>/f5/`)
```
/ruta/absoluta/exports/<nombre>/f5/wavs/filename.wav|transcripcion
```
Rutas absolutas requeridas.

### Common Voice (`exports/<nombre>/commonvoice/`)
TSV de 8 columnas (`client_id`, `path`, `sentence`, `up_votes`, `down_votes`, `age`, `gender`, `accent`). Se generan:
- `train.tsv` — 80%
- `dev.tsv` — 10%
- `test.tsv` — 10%
- `validated.tsv` — 100%

### CSV genérico (`exports/<nombre>/csv/`)
```
audio|transcripcion|idioma
```
Con cabecera. Para procesamiento personalizado o modelos no listados.

---

## 10. Autenticación y roles

### Roles

| rol         | permisos                                                        |
|-------------|-----------------------------------------------------------------|
| recolector  | Subir audios, registrar clips, ver clips propios                |
| revisor     | Todo lo anterior + corregir transcripciones de cualquier clip y desactivarlo (soft delete) |
| admin       | Todo lo anterior + gestionar usuarios, generar splits, borrar clips |

La gestión de usuarios (crear, listar, desactivar) es accesible tanto por la API (`/auth/usuarios`, ver Sección 5) como desde la pestaña "Gestión de usuarios" de la interfaz Gradio (ver Sección 7), visible únicamente para el rol `admin`.

### JWT

- Algoritmo: `HS256`
- Expiración: 480 minutos (8 horas, configurable en `.env` con `JWT_MINUTES`)
- Secret: variable `JWT_SECRET` en `.env` — **cambiar antes de desplegar en producción**

### Patrón de dependencias FastAPI (crítico)

```python
# api/auth.py define alias Annotated con Depends integrado
CurrentUser    = Annotated[Usuario, Depends(get_current_user)]
AdminUser      = Annotated[Usuario, Depends(require_rol("admin"))]
RevisorUser    = Annotated[Usuario, Depends(require_rol("admin", "revisor"))]

# En los routers: usar = None como default (el Depends ya está en el Annotated)
# MAL → AssertionError: Cannot specify Depends in Annotated and default value together
def endpoint(user: CurrentUser = Depends()):  # INCORRECTO
    ...

# BIEN
def endpoint(user: CurrentUser = None):       # CORRECTO
    ...
```

---

## 11. Configuración

### `.env` (raíz del proyecto)

```ini
DB_HOST=localhost
DB_PORT=5432
DB_NAME=corpus_tts
DB_USER=postgres
DB_PASS=tu_contraseña_segura
JWT_SECRET=cadena_aleatoria_de_al_menos_32_caracteres
JWT_MINUTES=480
API_BASE=http://127.0.0.1:8000

# HF_HOME=D:\otra\ruta          # opcional: sobreescribe .hf_cache/ del proyecto
# HF_TOKEN=hf_xxxx              # opcional: mayor velocidad de descarga en HuggingFace
HF_HUB_DISABLE_SYMLINKS_WARNING=1   # evita warning de symlinks en Windows
```

Crear este fichero como texto plano `KEY=VALUE`. En PowerShell usar `New-Item .env` y editar con un editor de texto, no con heredoc (`@"..."@`) porque añade caracteres de control que rompen `python-dotenv`.

### Caché de modelos HuggingFace

Los modelos de HuggingFace (XTTS v2, ~1.8 GB) se descargan en `.hf_cache/` dentro del proyecto. Esta carpeta viaja con el proyecto al copiarlo entre máquinas.

La lógica de selección de ruta en `gui_tts_andaluz.py` y `core/train.py` sigue este orden:
1. `HF_HOME` en `.env` — si existe y su unidad está disponible
2. `.hf_cache/` dentro del proyecto — fallback portable

Si en el sistema hay configurada una variable de entorno `HUGGINGFACE_HUB_CACHE` (p.ej. apuntando a una unidad de red), los scripts de arranque la sobreescriben a nivel de proceso antes de que Python empiece.

### `config.py` — constantes de audio

| constante         | valor por defecto | descripción                                |
|-------------------|-------------------|--------------------------------------------|
| `MAX_DURATION`    | 15 s              | Duración máxima por segmento               |
| `MIN_DURATION`    | 2 s               | Duración mínima (más cortos descartados)   |
| `SILENCE_THRESH`  | -40 dBFS          | Umbral de silencio para segmentación       |
| `MIN_SILENCE_MS`  | 400 ms            | Silencio mínimo para cortar               |
| `KEEP_SILENCE_MS` | 100 ms            | Padding de silencio a conservar            |
| `TRAIN_RATIO`     | 0.85              | Proporción train/eval en split             |
| `WAV_SR`          | 22050 Hz          | Sample rate de los WAVs exportados         |
| `WHISPER_DEFAULT` | `"large-v3"`      | Modelo Whisper por defecto                 |

---

## 12. Instalación

### Requisitos del sistema

- Python 3.10 o 3.11 (recomendado; ver nota 3.12 en sección 16)
- PostgreSQL 14+
- ffmpeg en PATH
- espeak-ng (solo para Piper)
- CUDA (solo para F5-TTS y XTTS con GPU)

### Entorno Python

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Dependencias del `requirements.txt`

```
fastapi
uvicorn[standard]
SQLAlchemy
python-dotenv
gradio
bcrypt
pydub
requests
psycopg2-binary
python-jose[cryptography]
python-multipart
openai-whisper
```

### Dependencias opcionales por modelo

**XTTS v2 (Python ≤ 3.11)**:
```bash
pip install TTS
```

**XTTS v2 (Python 3.12, fork Idiap)**:
```bash
pip install git+https://github.com/idiap/coqui-ai-TTS
pip install torch==2.7.1+cpu torchaudio==2.7.1+cpu \
    --index-url https://download.pytorch.org/whl/cpu
```

**Piper**:
```bash
pip install piper-tts piper-phonemize
# En el sistema: apt install espeak-ng / choco install espeak-ng
```

**F5-TTS**:
```bash
pip install f5-tts   # requiere CUDA
```

### Configuración de la base de datos

`install.ps1`/`install.sh` ya se encargan de esto automáticamente: comprueban `ffmpeg`/`espeak-ng`/`psql`, generan un `JWT_SECRET` aleatorio, crean la base de datos `corpus_tts` (en Linux también fijan la contraseña del rol `postgres` sin pedirla, usando autenticación *peer* local; en Windows piden la contraseña de `postgres` una vez para crearla y crear las tablas), y ofrecen crear el primer administrador de forma interactiva al final. Lo que sigue es la referencia manual, útil si el instalador no pudo completar algún paso:

```bash
# En PostgreSQL
psql -U postgres -c "CREATE DATABASE corpus_tts;"

# Crear tablas (desde la raíz del proyecto con venv activo)
python -c "from data.db import init_db; init_db()"

# Crear primer administrador
python -c "
from data.db import crear_usuario
crear_usuario('tu_uvus', 'Tu Nombre Completo', 'admin', 'tu_contraseña')
"

# Cargar topónimos NGA
python data/migrar_nga.py
```

---

## 13. Arranque del sistema

Se necesitan **dos terminales separadas**. Usar los scripts de arranque incluidos en lugar de lanzar Python directamente: los scripts fijan las variables de entorno de HuggingFace **antes** de que Python empiece, evitando conflictos con rutas configuradas a nivel de sistema (p.ej. unidades de red universitarias).

**Terminal 1 — API REST:**
```powershell
.\start_api.ps1
```

**Terminal 2 — Interfaz Gradio:**
```powershell
.\start_gui.ps1
```

La interfaz estará disponible en `http://localhost:7860`. La API en `http://localhost:8000`. La documentación de la API en `http://localhost:8000/docs`.

**Lanzado manual (alternativa):** activar el venv y ejecutar directamente — el código también lo gestiona, pero si el sistema tiene `HUGGINGFACE_HUB_CACHE` apuntando a una unidad no disponible puede aparecer un warning al arrancar:
```powershell
venv\Scripts\Activate.ps1
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload   # Terminal 1
python gui_tts_andaluz.py                                    # Terminal 2
```

**Nota**: lanzar la API antes que la GUI. La GUI intenta conectar a la API al arrancar para cargar el banner con estadísticas del dataset.

---

## 14. Convención de nombres de archivo

Los archivos WAV siguen el patrón:

```
{codigo_ine}_{codigo_hablante}_{tipo}{numero_zfill2}.wav
```

| campo           | ejemplo  | descripción                               |
|-----------------|----------|-------------------------------------------|
| `codigo_ine`    | `41095`  | Código INE del municipio (5 dígitos)      |
| `codigo_hablante`| `01`    | Código del hablante dentro del municipio  |
| `tipo`          | `seg`    | `seg` = segmento por silencio, `t` = por tiempo |
| `numero`        | `01`     | Número de segmento con cero a la izquierda|

Ejemplos:
- `41095_01_seg01.wav` — Sevilla capital, hablante 01, segmento 1
- `04101_02_t03.wav` — Viator (Almería), hablante 02, partición por tiempo 3

La ruta almacenada en BD (`nombre_archivo`) es siempre relativa: `wavs/41095_01_seg01.wav`.

La estructura de directorios en disco:
```
dataset/
└── Sevilla/
    └── Sevilla_41095/
        └── wavs/
            ├── 41095_01_seg01.wav
            ├── 41095_01_seg02.wav
            └── ...
```

El código de la primera parte del nombre de archivo permite filtrar por hablante en las exportaciones (`hablante_prefix = f"wavs/{codigo_ine}_{codigo_hablante}_"`).

---

## 15. Migración entre máquinas

### Qué transferir

| Qué | Cómo | Notas |
|-----|------|-------|
| Código fuente | `git clone` / copia de carpeta | Excluye lo del `.gitignore` |
| Base de datos | `scripts/db_backup.sh` → `.sql` | Transferir el `.sql` y restaurar con `db_restore.sh` |
| Dataset de audio | `rsync` / pendrive / scp | Carpeta `dataset/` — puede ser grande |
| Modelos HF (XTTS) | Copiar `.hf_cache/` | ~1.8 GB; si no se copia, se vuelven a descargar |
| `.env` | Copiar y editar | Ajustar credenciales de BD de la nueva máquina |
| `NGA_TOPONIMOS_*.csv` | Incluido en el código | Versión reducida a datos públicos de municipio (ver Sección 4) — sustituye por tu catálogo NGA completo si lo tienes |

### Procedimiento completo

**1. En la máquina origen — exportar BD:**
```bash
./scripts/db_backup.sh          # Linux
.\scripts\db_backup.ps1         # Windows
# Genera: scripts/backup_corpus_tts_YYYYMMDD_HHMMSS.sql
```

**2. Transferir al destino** (copiar carpeta del proyecto o `git clone` + copiar ficheros grandes):
```bash
# Con rsync (Linux → Linux):
rsync -av --exclude='venv/' --exclude='.hf_cache/' \
    /ruta/origen/Andalucia/ usuario@destino:/ruta/destino/Andalucia/

# O copiar manualmente: código + .sql + dataset/ + .env
```

**3. En la máquina destino — instalar:**
```bash
# Linux / Raspberry Pi:
chmod +x install.sh && ./install.sh

# Windows:
powershell -ExecutionPolicy Bypass -File install.ps1
```

**4. Editar `.env`** con las credenciales de PostgreSQL de la nueva máquina.

**5. Restaurar BD:**
```bash
./scripts/db_restore.sh scripts/backup_corpus_tts_YYYYMMDD_HHMMSS.sql
```

**6. Arrancar:**
```bash
./start_api.sh    # Terminal 1
./start_gui.sh    # Terminal 2
```

### Limitaciones por plataforma

| Característica | Windows | Linux x86 | Raspberry Pi (ARM) |
|---|---|---|---|
| API + Gradio | ✓ | ✓ | ✓ |
| Procesado audio + Whisper | ✓ (large-v3) | ✓ (large-v3) | ✓ (small/base) |
| XTTS v2 zero-shot | ✓ CPU lento | ✓ CPU lento | ✗ sin GPU |
| XTTS v2 fine-tuning | ✓ CPU muy lento | ✓ CPU muy lento | ✗ sin GPU |
| Piper (síntesis) | ✓ | ✓ | ✓ recomendado |
| F5-TTS | ✗ sin CUDA | ✓ con CUDA | ✗ sin GPU |

**Raspberry Pi**: úsala como servidor de recolección y revisión (API + Gradio + Whisper `small`). El entrenamiento y la síntesis XTTS hacerlos en un PC con más recursos.

---

## 16. Notas de despliegue

### Windows (desarrollo y producción local)

- Los symlinks requieren permisos de administrador o la política `SeCreateSymbolicLinkPrivilege`. Usar siempre `copy_mode="copy"` en Windows.
- PostgreSQL en Windows: instalar desde el instalador oficial (postgresql.org). El servicio arranca automáticamente.
- El proceso de Gradio y la API pueden registrarse como servicios Windows con `nssm` o `WinSW`.

### Raspberry Pi / servidor ARM de baja potencia

- Usar PostgreSQL 14 desde los repositorios ARM oficiales.
- Whisper `large-v3` es demasiado pesado. Usar `small` o `base` para transcripción.
- CoquiTTS/XTTS en CPU con 8 GB de RAM funciona pero lentamente (~30 s por frase).
- Piper es el modelo más adecuado para ARM: síntesis rápida, bajo consumo de memoria.
- F5-TTS requiere GPU y no es viable en RPi.

### Seguridad en producción

- Cambiar `JWT_SECRET` a una cadena aleatoria de ≥ 32 caracteres.
- Restringir `allow_origins` en el middleware CORS de `api/main.py`.
- Usar nginx como proxy inverso delante de ambos puertos (7860 y 8000).
- El puerto 7860 de Gradio tiene autenticación integrada (validada contra la API).
- No exponer `/docs` ni `/redoc` en producción (añadir middleware de IP restriction o autenticación adicional).

---

## 16. Problemas conocidos y soluciones

### Symlinks warning en Windows (`HF_HUB_DISABLE_SYMLINKS_WARNING`)

**Causa**: Windows requiere Modo Desarrollador o permisos de administrador para crear symlinks. `huggingface_hub` los usa por defecto para deduplicar ficheros en caché.

**Efecto**: solo warning, no error. La caché funciona igualmente usando copias.

**Solución**: ya incluido en `.env`:
```ini
HF_HUB_DISABLE_SYMLINKS_WARNING=1
```

### `HUGGINGFACE_HUB_CACHE` apunta a una unidad de red no disponible

**Síntoma**: al arrancar aparece `Ignored error while writing commit hash to M:\...` y la descarga de modelos falla.

**Causa**: variable de entorno del sistema (`HUGGINGFACE_HUB_CACHE` o `HF_HOME`) configurada para una unidad de red universitaria. `huggingface_hub` la lee al importarse (antes de cualquier código de usuario).

**Solución**: usar `.\start_gui.ps1` en lugar de `python gui_tts_andaluz.py`. El script fija `HF_HOME` a nivel de proceso antes de que Python arranque. Además `_setup_hf_cache()` en `gui_tts_andaluz.py` parchea `huggingface_hub.constants` directamente si ya estaba cargado por algún `.pth` del venv.

### `AssertionError: Cannot specify Depends in Annotated and default value together`

**Causa**: usar `= Depends()` como default en parámetros cuyo tipo `Annotated` ya contiene un `Depends`.

**Solución**: usar `= None`. FastAPI honra el `Depends` del `Annotated` ignorando el `None`.

```python
# INCORRECTO
def endpoint(user: CurrentUser = Depends()):
    ...

# CORRECTO
def endpoint(user: CurrentUser = None):
    ...
```

### `python-dotenv` falla al parsear `.env`

**Causa**: el fichero `.env` fue creado con sintaxis PowerShell heredoc (`@"..."`), que embebe caracteres de control invisibles.

**Solución**: recrear el `.env` como texto plano con un editor de texto. Verificar con `type .env` (Windows) que cada línea tiene formato `KEY=VALUE` sin caracteres extraños.

### `pip install TTS` falla en Python 3.12

**Causa**: CoquiTTS oficial no publica wheels para Python 3.12.

**Solución**:
```bash
pip install git+https://github.com/idiap/coqui-ai-TTS
pip install torch==2.7.1+cpu torchaudio==2.7.1+cpu \
    --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
```
Versiones de torch y torchaudio deben coincidir en major.minor.

### Las exportaciones muestran transcripciones de Whisper, no las corregidas

**Causa**: la función de exportación leía el CSV local, que nunca se actualiza con correcciones manuales (las correcciones solo van a la BD).

**Solución**: ya corregido en `data/export.py` con `_enrich_rows_from_db()`. Si el problema reaparece, verificar que la BD es accesible desde el proceso que ejecuta la exportación y que los `nombre_archivo` en el CSV coinciden exactamente con los de la BD (incluyendo el prefijo `wavs/`).

### `UserWarning: theme parameter moved to launch()`

**Causa**: Gradio 6 movió el parámetro `theme` de `gr.Blocks()` a `app.launch()`.

**Solución** (ya aplicada en `gui_tts_andaluz.py`):
```python
with gr.Blocks(title="...") as app:  # Sin theme aquí
    ...
app.launch(..., theme=gr.themes.Soft(primary_hue="orange"))
```

### El selector de clips en zero-shot muestra transcripciones incorrectas / solo un clip

**Causa**: la función original auto-seleccionaba un clip y leía la transcripción del CSV.

**Solución** (ya aplicada en `ui/tab_entrenar.py`): `get_clips_de_hablante()` consulta la BD, devuelve todos los clips activos del hablante con las transcripciones corregidas. El selector `clip_zs_dd` muestra todos los clips con formato `"filename.wav · Xs — transcripcion..."`.

### `[ERROR] CoquiTTS no instalado` aunque ya está instalado

**Causa**: la librería TTS importa `torchaudio` internamente; si no está instalado la importación falla con un error que se captura mostrando el mensaje genérico de "no instalado".

**Solución**: instalar `torchaudio` de la versión correcta (debe coincidir en major.minor con `torch`):
```bash
pip install torchaudio==2.7.1+cpu --index-url https://download.pytorch.org/whl/cpu
```

---

## Metodología y continuidad del TFG

El ciclo de trabajo es:

1. **Recolección**: voluntarios graban lectura de textos con vocabulario local. Los recolectores suben los archivos a través de la pestaña "Procesar audios".

2. **Procesado automático**: segmentación por silencio, normalización de audio a 22050 Hz mono, transcripción Whisper con prompt de topónimos locales del NGA.

3. **Revisión colaborativa**: revisores corrigen transcripciones incorrectas. Cada corrección queda registrada en la tabla `correccion` con auditoría completa (quién, cuándo, qué cambió).

4. **Control de calidad**: pestaña "Estadísticas" para ver el volumen de datos por hablante y decidir cuándo hay suficiente material para entrenar.

5. **Exportación**: la pestaña "Exportar" genera el dataset en el formato del modelo objetivo con las transcripciones corregidas de la BD.

6. **Entrenamiento**: fine-tuning o zero-shot desde la pestaña "Entrenar". Los resultados pueden probarse directamente en el Paso 4 (síntesis de prueba).

### Para nuevos agentes o desarrolladores

Los puntos de entrada para entender el código:

- **Modelos de datos** → `data/db.py` (SQLAlchemy ORM)
- **API endpoints** → `api/routes/*.py`
- **Lógica de audio** → `core/audio.py`, `core/transcripcion.py`
- **Lógica de entrenamiento** → `core/train.py`
- **Pipeline de exportación** → `data/export.py` (leer `_enrich_rows_from_db` primero)
- **UI** → `ui/tab_*.py` (cada pestaña es un módulo independiente)
- **Punto de entrada Gradio** → `gui_tts_andaluz.py`
- **Punto de entrada API** → `api/main.py`

La invariante más importante: **el CSV local es solo de Whisper**. Todo lo que lee o escribe transcripciones corregidas debe usar la BD, no el CSV.
