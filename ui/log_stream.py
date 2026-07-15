"""
ui/log_stream.py

Buffer de logs en memoria para el panel «Logs» en vivo de la interfaz.

Captura todo lo que la aplicación escribe por stdout/stderr (prints propios,
warnings de torch, salida del entrenador de Coqui, tracebacks) en un búfer
circular que la pestaña «Logs» de Gradio consulta periódicamente. Sin esto, la
salida del backend solo era visible en la terminal desde la que se arranca la
GUI, y los errores que ocurrían dentro de un callback se perdían sin dejar
rastro en la interfaz.
"""

import sys
import threading
from collections import deque

# Búfer circular: se queda con las últimas _MAXLINES líneas para no crecer sin
# límite durante un entrenamiento largo.
_MAXLINES = 3000
_LOCK = threading.Lock()
_BUFFER: deque = deque(maxlen=_MAXLINES)
_partial = ""          # línea a medio escribir (writes sin '\n' todavía)
_installed = False


def _append(text: str) -> None:
    """Trocea el texto entrante en líneas completas y las añade al búfer.

    Gestiona '\\r' (barras de progreso tipo tqdm del entrenador) quedándose solo
    con el último segmento de la línea, para no inundar el búfer con cada
    fotograma de la barra.
    """
    global _partial
    if not text:
        return
    with _LOCK:
        _partial += text
        _partial = _partial.replace("\r\n", "\n")
        while "\n" in _partial:
            line, _partial = _partial.split("\n", 1)
            if "\r" in line:
                line = line.split("\r")[-1]
            _BUFFER.append(line)
        if len(_partial) > 8000:      # cota de seguridad para líneas sin salto
            _partial = _partial[-8000:]


class _Tee:
    """Envuelve un stream (stdout/stderr) para escribir a la vez en el original
    y en el búfer de logs, sin alterar el comportamiento en la terminal."""

    def __init__(self, original):
        self._original = original

    def write(self, data):
        try:
            self._original.write(data)
        except Exception:
            pass
        try:
            _append(data if isinstance(data, str) else str(data))
        except Exception:
            pass
        return len(data)

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        # Delegar isatty, encoding, fileno, etc. al stream real.
        return getattr(self._original, name)


def install() -> None:
    """Redirige stdout y stderr a través del _Tee. Idempotente."""
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)


def get_log_text() -> str:
    """Texto completo del búfer, incluida la línea parcial en curso."""
    with _LOCK:
        lines = list(_BUFFER)
        tail = _partial.split("\r")[-1] if _partial.strip() else ""
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def clear() -> str:
    """Vacía el búfer. Devuelve cadena vacía para poder cablearlo directo a la caja."""
    global _partial
    with _LOCK:
        _BUFFER.clear()
        _partial = ""
    return ""
