"""
data/db.py
Modelos SQLAlchemy y utilidades de base de datos para el corpus TTS Andaluz.
Usa PostgreSQL. Configuración mediante fichero .env en la raíz del proyecto.
"""

import os
import bcrypt
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    Text, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ---------------------------------------------------------------------------
# Configuración desde .env
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "corpus_tts")
DB_USER = os.getenv("DB_USER", "tts_user")
DB_PASS = os.getenv("DB_PASS", "tts_pass")

DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

from sqlalchemy.exc import OperationalError as SAOperationalError

# Intentar conectar a PostgreSQL; si falla, usar fallback a SQLite local para desarrollo.
try:
    engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)
    # probar conexión rápida
    with engine.connect() as conn:
        pass
    print(f" + Conectado a PostgreSQL en {DB_HOST}:{DB_PORT}/{DB_NAME}")
except Exception as e:  # pragma: no cover - ambiente local/CI puede no tener PG
    print(f" ! No se pudo conectar a PostgreSQL ({e}). Usando fallback SQLite local.")
    sqlite_path = Path(__file__).parent.parent / "data_dev.sqlite3"
    sqlite_url = f"sqlite:///{sqlite_path.as_posix()}"
    engine = create_engine(sqlite_url, echo=False, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base         = declarative_base()


@contextmanager
def get_session() -> Session:
    """Context manager para sesiones seguras con rollback automático."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
# Añadir en data/db.py, después del contextmanager get_session

def get_db():
    """Generador para inyección de dependencias en FastAPI (Depends)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class Provincia(Base):
    __tablename__ = "provincia"

    id     = Column(Integer,    primary_key=True, autoincrement=True)
    codigo = Column(String(2),  nullable=False, unique=True)
    nombre = Column(String(50), nullable=False)

    municipios = relationship("Municipio", back_populates="provincia")

    def __repr__(self):
        return f"<Provincia {self.codigo} - {self.nombre}>"


class Municipio(Base):
    __tablename__ = "municipio"

    id             = Column(Integer,      primary_key=True, autoincrement=True)
    provincia_id   = Column(Integer,      ForeignKey("provincia.id"), nullable=False)
    codigo_ine     = Column(String(5),    nullable=False, unique=True)
    nombre         = Column(String(100),  nullable=False)
    nombre_oficial = Column(String(100))
    latitud        = Column(Float)
    longitud       = Column(Float)
    altitud        = Column(Float)

    provincia = relationship("Provincia", back_populates="municipios")
    hablantes = relationship("Hablante",  back_populates="municipio")
    toponimos = relationship("Toponimo",  back_populates="municipio")

    def __repr__(self):
        return f"<Municipio {self.codigo_ine} - {self.nombre}>"


class Toponimo(Base):
    __tablename__ = "toponimo"
    __table_args__ = (
        UniqueConstraint("municipio_id", "nombre"),
    )

    id           = Column(Integer,      primary_key=True, autoincrement=True)
    municipio_id = Column(Integer,      ForeignKey("municipio.id"), nullable=False)
    nombre       = Column(String(200),  nullable=False)
    estado       = Column(String(20),   nullable=False, default="normalizado")

    municipio = relationship("Municipio", back_populates="toponimos")

    def __repr__(self):
        return f"<Toponimo {self.nombre} ({self.estado})>"


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (
        CheckConstraint("rol IN ('recolector', 'revisor', 'admin')", name="ck_rol"),
    )

    id            = Column(Integer,     primary_key=True, autoincrement=True)
    uvus          = Column(String(50),  nullable=False, unique=True)
    nombre        = Column(String(100), nullable=False)
    rol           = Column(String(20),  nullable=False)
    password_hash = Column(String(255), nullable=False)
    activo        = Column(Boolean,     nullable=False, default=True)
    creado_en     = Column(DateTime,    nullable=False, default=lambda: datetime.now(timezone.utc))
    ultimo_acceso = Column(DateTime)

    clips_creados = relationship("Clip",       foreign_keys="Clip.creado_por_id",
                                 back_populates="creado_por")
    correcciones  = relationship("Correccion", back_populates="usuario")

    def set_password(self, password: str):
        self.password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def __repr__(self):
        return f"<Usuario {self.uvus} ({self.rol})>"


class Hablante(Base):
    __tablename__ = "hablante"
    __table_args__ = (
        UniqueConstraint("municipio_id", "codigo"),
        CheckConstraint("genero IN ('M', 'F', 'X')", name="ck_genero"),
    )

    id           = Column(Integer,   primary_key=True, autoincrement=True)
    municipio_id = Column(Integer,   ForeignKey("municipio.id"), nullable=False)
    usuario_id   = Column(Integer,   ForeignKey("usuario.id"))
    codigo       = Column(String(2), nullable=False)
    edad         = Column(Integer)
    genero       = Column(String(1))

    municipio = relationship("Municipio", back_populates="hablantes")
    usuario   = relationship("Usuario")
    clips     = relationship("Clip",      back_populates="hablante")

    def __repr__(self):
        return f"<Hablante {self.codigo} - {self.municipio.nombre if self.municipio else '?'}>"


class Clip(Base):
    __tablename__ = "clip"
    __table_args__ = (
        CheckConstraint("split IN ('train', 'eval')", name="ck_split"),
    )

    id             = Column(Integer,     primary_key=True, autoincrement=True)
    hablante_id    = Column(Integer,     ForeignKey("hablante.id"),  nullable=False)
    creado_por_id  = Column(Integer,     ForeignKey("usuario.id"),   nullable=False)
    nombre_archivo = Column(String(200), nullable=False, unique=True)
    transcripcion  = Column(Text)
    idioma         = Column(String(2),   nullable=False, default="es")
    duracion_s     = Column(Float)
    split          = Column(String(5))
    activo         = Column(Boolean,     nullable=False, default=True)
    creado_en      = Column(DateTime,    nullable=False, default=lambda: datetime.now(timezone.utc))
    actualizado_en = Column(DateTime,    nullable=False, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    hablante     = relationship("Hablante",   back_populates="clips")
    creado_por   = relationship("Usuario",    foreign_keys=[creado_por_id],
                                back_populates="clips_creados")
    correcciones = relationship("Correccion", back_populates="clip")

    def __repr__(self):
        return f"<Clip {self.nombre_archivo} ({self.split})>"


class Correccion(Base):
    __tablename__ = "correccion"

    id             = Column(Integer,  primary_key=True, autoincrement=True)
    clip_id        = Column(Integer,  ForeignKey("clip.id"),    nullable=False)
    usuario_id     = Column(Integer,  ForeignKey("usuario.id"), nullable=False)
    texto_anterior = Column(Text)
    texto_nuevo    = Column(Text)
    creado_en      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    clip    = relationship("Clip",    back_populates="correcciones")
    usuario = relationship("Usuario", back_populates="correcciones")

    def __repr__(self):
        return f"<Correccion clip={self.clip_id} por={self.usuario_id}>"


# ---------------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------------
Index("idx_municipio_provincia", Municipio.provincia_id)
Index("idx_toponimo_municipio",  Toponimo.municipio_id)
Index("idx_hablante_municipio",  Hablante.municipio_id)
Index("idx_clip_hablante",       Clip.hablante_id)
Index("idx_clip_split",          Clip.split)
Index("idx_clip_activo",         Clip.activo)
Index("idx_correccion_clip",     Correccion.clip_id)
Index("idx_correccion_usuario",  Correccion.usuario_id)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def init_db():
    """Crea todas las tablas si no existen. Seguro de llamar múltiples veces."""
    Base.metadata.create_all(bind=engine)
    print(f" + Base de datos inicializada: {engine.url}")


def verificar_credenciales(uvus: str, password: str) -> bool:
    """Autenticación para app.launch(auth=...) de Gradio."""
    with get_session() as session:
        usuario = session.query(Usuario).filter_by(uvus=uvus, activo=True).first()
        if not usuario:
            return False
        ok = usuario.check_password(password)
        if ok:
            usuario.ultimo_acceso = datetime.now(timezone.utc)
        return ok


def crear_usuario(uvus: str, nombre: str, rol: str, password: str) -> Usuario:
    """Crea un usuario nuevo con contraseña hasheada."""
    with get_session() as session:
        usuario = Usuario(uvus=uvus, nombre=nombre, rol=rol)
        usuario.set_password(password)
        session.add(usuario)
        session.flush()
        print(f" + Usuario creado: {uvus} ({rol})")
        return usuario


def get_whisper_prompt_municipio(municipio_id: int, max_chars: int = 1000) -> str:
    """Prompt de topónimos NGA para Whisper desde la base de datos."""
    with get_session() as session:
        tops = (
            session.query(Toponimo.nombre)
            .filter_by(municipio_id=municipio_id)
            .all()
        )
        nombres = [t.nombre for t in tops]
        partes, total = [], 0
        for n in sorted(nombres, key=lambda x: -len(x)):
            if total + len(n) + 2 > max_chars:
                break
            partes.append(n)
            total += len(n) + 2
        return ", ".join(partes) + "." if partes else ""


def export_splits(split: str = "train") -> list[dict]:
    """Clips de un split como lista de dicts, compatible con LJSpeech."""
    with get_session() as session:
        clips = (
            session.query(Clip)
            .filter_by(split=split, activo=True)
            .all()
        )
        return [
            {
                "audio":         c.nombre_archivo,
                "transcripcion": c.transcripcion,
                "idioma":        c.idioma,
                "municipio":     c.hablante.municipio.nombre,
                "provincia":     c.hablante.municipio.provincia.nombre,
            }
            for c in clips
        ]


def clips_sin_revisar() -> list:
    """Clips activos sin ninguna corrección asociada. Devuelve dicts para evitar instancias detached."""
    with get_session() as session:
        clips = (
            session.query(Clip)
            .outerjoin(Correccion)
            .filter(Correccion.id == None, Clip.activo == True)
            .all()
        )
        return [
            {
                "id": c.id,
                "nombre_archivo": c.nombre_archivo,
                "transcripcion": c.transcripcion,
                "hablante_id": c.hablante_id,
                "duracion_s": c.duracion_s,
            }
            for c in clips
        ]


# ---------------------------------------------------------------------------
# Arranque directo: python data/db.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    print(" Tablas creadas. Para crear un admin:")
    print('   from data.db import crear_usuario')
    print('   crear_usuario("tu_uvus", "Tu Nombre", "admin", "contraseña")')