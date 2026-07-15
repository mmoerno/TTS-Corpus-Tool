"""
core/train.py
Entrenamiento y síntesis para XTTS v2, Piper y F5-TTS.

Cada trainer expone:
  train_local()   — entrenamiento en la máquina actual
  train_remote()  — stub (endpoint FastAPI pendiente)
  synthesize()    — prueba de síntesis con el modelo entrenado
"""
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from contextlib import contextmanager
import subprocess
import sys
import os
import time

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


_TRAINING_LOCK_PATH = Path(__file__).parent.parent / ".training.lock"


def _pid_alive(pid: int) -> bool:
    """Comprueba si un PID sigue vivo, en Windows y Linux, sin depender de psutil."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


class TrainingInProgressError(Exception):
    """Ya hay un entrenamiento local en curso (mismo o distinto modelo)."""


@contextmanager
def training_lock():
    """
    Impide que dos entrenamientos locales corran a la vez (dos invocaciones
    concurrentes agotan la RAM al cargar cada una su propia copia del modelo,
    y además chocan al escribir en el mismo fichero de log). Se ha visto en
    la práctica con reintentos automáticos de Gradio tras un corte de
    conexión (frecuente en sesiones largas sobre escritorio remoto/VPN), que
    reenvían la misma petición de entrenamiento sin que el usuario haga nada.

    Usa un fichero de bloqueo con el PID del proceso propietario; si el
    proceso que lo creó ya no existe (crash sin limpiar), el bloqueo se
    considera obsoleto y se recupera automáticamente.
    """
    if _TRAINING_LOCK_PATH.exists():
        try:
            owner_pid = int(_TRAINING_LOCK_PATH.read_text().strip())
        except (ValueError, OSError):
            owner_pid = None
        if owner_pid and _pid_alive(owner_pid):
            raise TrainingInProgressError(
                f"Ya hay un entrenamiento en curso (proceso {owner_pid}). "
                "Espera a que termine antes de lanzar otro."
            )
        # Bloqueo obsoleto de un proceso que ya no existe: se recupera.
        _TRAINING_LOCK_PATH.unlink(missing_ok=True)

    try:
        # Creación exclusiva: falla si otro proceso lo crea entre el chequeo
        # de arriba y este punto (evita la ventana de carrera TOCTOU).
        fd = os.open(_TRAINING_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        raise TrainingInProgressError(
            "Ya hay un entrenamiento en curso (arrancado justo ahora por otro proceso). "
            "Espera a que termine antes de lanzar otro."
        )

    try:
        yield
    finally:
        _TRAINING_LOCK_PATH.unlink(missing_ok=True)


def _fix_hf_cache():
    """
    Asegura que HF_HOME y huggingface_hub.constants apuntan a .hf_cache/
    dentro del proyecto. Parchea el módulo si ya estaba importado.
    """
    from config import HF_CACHE as _project_hf_cache

    env_hf = os.environ.get("HF_HOME", "")
    if env_hf:
        drive = Path(env_hf).drive
        drive_ok = (not drive) or Path(drive + "\\").exists()
    else:
        drive_ok = False

    if drive_ok:
        target = Path(env_hf)
    else:
        if env_hf:
            print(f"[train] HF_HOME='{env_hf}' no disponible, usando .hf_cache/")
        target = _project_hf_cache

    target.mkdir(parents=True, exist_ok=True)
    hub = str(target / "hub")

    os.environ["HF_HOME"] = str(target)
    os.environ["HUGGINGFACE_HUB_CACHE"] = hub
    os.environ["HF_HUB_CACHE"] = hub

    # Parchear huggingface_hub.constants si ya está importado
    _const = sys.modules.get("huggingface_hub.constants")
    if _const is not None:
        _const.HF_HOME = str(target)
        if hasattr(_const, "HF_HUB_CACHE"):
            _const.HF_HUB_CACHE = hub
        if hasattr(_const, "HUGGINGFACE_HUB_CACHE"):
            _const.HUGGINGFACE_HUB_CACHE = hub

    print(f"[train] Cache HF: {target}")


_fix_hf_cache()


def _patch_symlinks_windows():
    """
    En Windows sin Modo Desarrollador, os.symlink lanza WinError 1314.
    huggingface_hub usa symlinks en su caché y no siempre captura ese error,
    haciendo fallar la descarga de modelos. Este parche reemplaza os.symlink
    con una versión que copia el fichero cuando el symlink no está disponible.
    Solo activo en Windows.
    """
    if sys.platform != "win32":
        return
    import shutil as _shutil
    _orig_symlink = os.symlink

    def _safe_symlink(src, dst, target_is_directory=False, *, dir_fd=None):
        try:
            _orig_symlink(src, dst, target_is_directory=target_is_directory, dir_fd=dir_fd)
        except OSError:
            src_str = str(src)
            dst_str = str(dst)
            if not os.path.isabs(src_str):
                src_str = os.path.normpath(os.path.join(os.path.dirname(dst_str), src_str))
            if os.path.isdir(src_str):
                if not os.path.exists(dst_str):
                    _shutil.copytree(src_str, dst_str)
            elif os.path.isfile(src_str):
                _shutil.copy2(src_str, dst_str)

    os.symlink = _safe_symlink


_patch_symlinks_windows()


def _patch_trainer_cleanup_windows():
    """
    Si el entrenamiento falla antes de guardar el primer checkpoint, el
    Trainer de Coqui borra la carpeta del run (remove_experiment_folder),
    que incluye trainer_0_log.txt: el propio fichero de log que el Trainer
    mantiene abierto vía logging.FileHandler durante toda la ejecución. En
    Windows no se puede borrar un fichero abierto por el propio proceso, así
    que ese borrado lanza WinError 32 dentro del "except BaseException" de
    Trainer.fit() — justo antes de traceback.print_exc(). Resultado: el
    error real por el que falló el entrenamiento nunca llega a imprimirse,
    y solo se ve el WinError 32 de la limpieza (visto en pruebas reales).
    Este parche cierra ese FileHandler antes de borrar la carpeta, para que
    la limpieza no reviente y el error real sí se imprima.
    """
    if sys.platform != "win32":
        return
    import logging

    try:
        import trainer.trainer as _trainer_mod
        from trainer import generic_utils as _generic_utils
    except ImportError:
        return

    _orig_remove = _generic_utils.remove_experiment_folder

    def _safe_remove(experiment_path):
        exp_str = str(experiment_path)
        trainer_logger = logging.getLogger("trainer")
        for h in list(trainer_logger.handlers):
            base = getattr(h, "baseFilename", None)
            if base and str(base).startswith(exp_str):
                try:
                    h.close()
                    trainer_logger.removeHandler(h)
                except Exception:
                    pass
        try:
            _orig_remove(experiment_path)
        except OSError as _rm_err:
            print(f"[train] No se pudo limpiar la carpeta del run fallido: {_rm_err}")

    _generic_utils.remove_experiment_folder = _safe_remove
    _trainer_mod.remove_experiment_folder = _safe_remove


def _patch_xtts_audio_loading():
    """
    torchaudio.load() puede delegar en el backend "torchcodec", que carga
    sus propias librerías FFmpeg vía ctypes; si esa carga falla (FFmpeg
    ausente o de una versión no soportada), TODAS las llamadas a
    torchaudio.load() fallan por igual — no solo un fichero puntual. El
    dataset de XTTS trata cualquier fallo de carga como "muestra corrupta"
    y reintenta con otra muestra al azar recursivamente (dataset.py,
    "return self[1]" dentro de un except desnudo); si el fallo es
    sistémico, esos reintentos nunca tienen éxito y la recursión sin fin
    agota la memoria del proceso (visto en pruebas reales: termina en
    MemoryError en vez de un error claro). Este parche sustituye la carga
    de audio de XTTS para usar soundfile directamente (ya es dependencia
    del proyecto), sin pasar por torchaudio/torchcodec/FFmpeg.
    """
    try:
        import soundfile as _sf
        import torch as _torch
        import torchaudio as _torchaudio
        from TTS.tts.models import xtts as _xtts_mod
        from TTS.tts.layers.xtts.trainer import dataset as _dataset_mod
    except ImportError:
        return

    def _load_audio_soundfile(audiopath, sampling_rate):
        data, lsr = _sf.read(str(audiopath), dtype="float32", always_2d=True)
        audio = _torch.from_numpy(data.T)
        if audio.size(0) != 1:
            audio = _torch.mean(audio, dim=0, keepdim=True)
        if lsr != sampling_rate:
            audio = _torchaudio.functional.resample(audio, lsr, sampling_rate)
        audio.clip_(-1, 1)
        return audio

    _xtts_mod.load_audio = _load_audio_soundfile
    _dataset_mod.load_audio = _load_audio_soundfile


TrainResult = Tuple[bool, List[str]]


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# XTTS v2
# ---------------------------------------------------------------------------

class XTTSTrainer:
    """Fine-tuning de XTTS v2 mediante CoquiTTS."""

    @staticmethod
    def required_format() -> str:
        return "xtts"

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> bool:
        return all(k in config for k in {"train_csv", "eval_csv", "output_dir"})

    @staticmethod
    def train_local(config: Dict[str, Any], progress_callback=None) -> TrainResult:
        log = []

        def L(msg):
            log.append(msg)
            if progress_callback:
                progress_callback("\n".join(log))

        L("Iniciando entrenamiento XTTS v2 local...")

        try:
            from TTS.config.shared_configs import BaseDatasetConfig
            from TTS.tts.datasets import load_tts_samples
            from TTS.tts.layers.xtts.trainer.gpt_trainer import (
                GPTArgs, GPTTrainer, GPTTrainerConfig,
            )
            try:
                from TTS.trainer import Trainer, TrainerArgs
            except ImportError:
                # Algunas versiones/forks de TTS no reexportan Trainer/TrainerArgs
                # desde TTS.trainer; viven en el paquete aparte "trainer" (Coqui).
                from trainer import Trainer, TrainerArgs
            _patch_trainer_cleanup_windows()
            _patch_xtts_audio_loading()
        except ImportError as _e:
            L(f"[ERROR] CoquiTTS no disponible: {_e}")
            L("  Python ≤3.11 : pip install TTS")
            L("  Python  3.12 : pip install git+https://github.com/idiap/coqui-ai-TTS")
            L("  Reinicia la app Gradio tras instalar.")
            return False, log

        try:
            _lock = training_lock()
            _lock.__enter__()
        except TrainingInProgressError as e:
            L(f"[ERROR] {e}")
            return False, log

        try:
            train_csv  = Path(config["train_csv"])
            eval_csv   = Path(config["eval_csv"])
            output_dir = Path(config["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            dataset_root = train_csv.parent

            L(f"Train CSV   : {train_csv}")
            L(f"Eval  CSV   : {eval_csv}")
            L(f"Dataset root: {dataset_root}")
            L(f"Salida      : {output_dir}")
            L(f"Épocas      : {config.get('epochs', 10)}")
            L(f"Batch       : {config.get('batch_size', 2)}")
            L(f"LR          : {config.get('lr', '5e-6')}")

            # El fine-tuning de XTTS v2 se hace sobre el componente GPT, y exige
            # partir de un checkpoint base real (dvae.pth, mel_stats.pth,
            # model.pth, vocab.json) — entrenar el GPT desde cero no es viable
            # con un corpus pequeño, así que aquí es obligatorio.
            checkpoint_dir = config.get("xtts_checkpoint_dir")
            if not checkpoint_dir:
                L("[ERROR] 'xtts_checkpoint_dir' no especificado.")
                L("  El fine-tuning de XTTS v2 requiere el checkpoint base de coqui/XTTS-v2.")
                L("  Descárgalo, p.ej.: from huggingface_hub import snapshot_download; "
                  "snapshot_download('coqui/XTTS-v2', local_dir='...')")
                return False, log

            checkpoint_dir = Path(checkpoint_dir)
            dvae_checkpoint = checkpoint_dir / "dvae.pth"
            mel_norm_file   = checkpoint_dir / "mel_stats.pth"
            xtts_checkpoint = checkpoint_dir / "model.pth"
            tokenizer_file  = checkpoint_dir / "vocab.json"
            faltantes = [
                str(p) for p in (dvae_checkpoint, mel_norm_file, xtts_checkpoint, tokenizer_file)
                if not p.exists()
            ]
            if faltantes:
                L(f"[ERROR] Faltan ficheros del checkpoint base en {checkpoint_dir}:")
                for f in faltantes:
                    L(f"  - {f}")
                return False, log
            L(f"Checkpoint base: {checkpoint_dir}")

            # ── Detección de dispositivo y optimización CPU ──────────────────
            use_cuda = _has_cuda()
            try:
                import torch as _torch
                device = "cuda" if use_cuda else "cpu"
                L(f"Dispositivo : {device.upper()}")
                n_loader = 4
                if device == "cpu":
                    n_cpus = os.cpu_count() or 4
                    # i9-12900K y similares: reservar 4 hilos para inter-op y SO
                    n_intra = max(1, n_cpus - 4)
                    n_inter = min(4, n_cpus)
                    _torch.set_num_threads(n_intra)
                    _torch.set_num_interop_threads(n_inter)
                    os.environ.setdefault("OMP_NUM_THREADS", str(n_intra))
                    os.environ.setdefault("MKL_NUM_THREADS", str(n_intra))
                    L(f"  Hilos PyTorch: {n_intra} intra-op + {n_inter} inter-op ({n_cpus} lógicos)")
                    L("[AVISO] Entrenamiento en CPU (sin GPU/CUDA detectada).")
                    L("  Tiempo estimado: 10 épocas con dataset pequeño ≈ 2–8 horas.")
                    L("  Con GPU CUDA el mismo entrenamiento tomaría ~15 minutos.")
                    L("  Consejo: batch_size=4 es seguro con tu RAM (≥64 GB).")
                    # En Windows, multiprocessing usa "spawn": cada DataLoader
                    # worker re-ejecuta el script principal desde cero (import de
                    # torch/TTS, carga del NGA, e incluso volver a levantar la app
                    # Gradio dentro de Gradio). Con un modelo de cientos de millones
                    # de parámetros, varios workers cargando su propia copia han
                    # llegado a agotar la RAM del sistema y matar el proceso
                    # (visto en pruebas reales). num_loader_workers=0 evita el
                    # multiprocessing por completo (carga de datos más lenta, pero
                    # sin duplicar procesos ni memoria).
                    n_loader = 0 if sys.platform == "win32" else min(8, n_cpus // 3)
            except Exception as _te:
                L(f"[INFO] Configuración de hilos PyTorch omitida: {_te}")
            # ─────────────────────────────────────────────────────────────────

            dataset_config = BaseDatasetConfig(
                formatter="ljspeech",
                meta_file_train=str(train_csv),
                meta_file_val=str(eval_csv),
                path=str(dataset_root),
                language="es",
            )

            model_args = GPTArgs(
                max_conditioning_length=132300,
                min_conditioning_length=66150,
                debug_loading_failures=False,
                max_wav_length=255995,
                max_text_length=200,
                mel_norm_file=str(mel_norm_file),
                dvae_checkpoint=str(dvae_checkpoint),
                xtts_checkpoint=str(xtts_checkpoint),
                tokenizer_file=str(tokenizer_file),
                gpt_num_audio_tokens=1026,
                gpt_start_audio_token=1024,
                gpt_stop_audio_token=1025,
                gpt_use_masking_gt_prompt_approach=True,
                gpt_use_perceiver_resampler=True,
            )
            batch_size = int(config.get("batch_size", 2))
            gpt_config = GPTTrainerConfig(
                output_path=str(output_dir),
                model_args=model_args,
                run_name="xtts_andaluz",
                project_name="corpus_tts_andaluz",
                run_description="Fine-tuning XTTS v2 sobre corpus dialectal andaluz",
                epochs=int(config.get("epochs", 10)),
                batch_size=batch_size,
                eval_batch_size=batch_size,
                num_loader_workers=n_loader,
                eval_split_max_size=256,
                print_step=50,
                save_step=1000,
                save_n_checkpoints=1,
                save_checkpoints=True,
                print_eval=False,
                optimizer="AdamW",
                optimizer_wd_only_on_weights=True,
                optimizer_params={"betas": [0.9, 0.96], "eps": 1e-8, "weight_decay": 1e-2},
                lr=float(config.get("lr", "5e-6")),
                lr_scheduler="MultiStepLR",
                lr_scheduler_params={"milestones": [50000, 150000, 300000], "gamma": 0.5, "last_epoch": -1},
                # Sin GPU: mixed precision requiere CUDA; sin él puede lanzar
                # RuntimeError ("Expected all tensors to be on the same device")
                mixed_precision=use_cuda,
            )

            gpt_config.datasets = [dataset_config]
            model = GPTTrainer.init_from_config(gpt_config)

            train_samples, eval_samples = load_tts_samples(
                gpt_config,
                eval_split=True,
                eval_split_max_size=gpt_config.eval_split_max_size,
                eval_split_size=gpt_config.eval_split_size,
            )
            L(f"Muestras: {len(train_samples)} train / {len(eval_samples)} eval")

            try:
                speaker_ref = train_samples[0]["audio_file"]
                gpt_config.test_sentences = [{
                    "text": "Esta es una prueba de síntesis de voz en andaluz.",
                    "speaker_wav": [speaker_ref],
                    "language": "es",
                }]
            except Exception:
                pass  # Frases de prueba solo para monitorización; no bloquean el entrenamiento

            trainer = Trainer(
                TrainerArgs(),
                gpt_config,
                output_path=str(output_dir),
                model=model,
                train_samples=train_samples,
                eval_samples=eval_samples,
            )

            L("Entrenando...")
            trainer.fit()

            # Coqui no copia vocab.json a la carpeta del run entrenado: solo
            # queda la ruta original del checkpoint base de XTTS-v2 grabada
            # en config.json (model_args.tokenizer_file), que puede no existir
            # si el modelo se traslada a otra máquina. Copiarlo aquí hace el
            # run autocontenido y portable para synthesize().
            try:
                import shutil as _shutil_copy
                _shutil_copy.copy2(tokenizer_file, trainer.output_path / "vocab.json")
            except Exception as _copy_err:
                L(f"[AVISO] No se pudo copiar vocab.json al run: {_copy_err}")

            L(f"[OK] Modelo guardado en: {output_dir}")
            return True, log

        except Exception as e:
            L(f"[ERROR] {e}")
            return False, log
        finally:
            _lock.__exit__(None, None, None)

    @staticmethod
    def synthesize(
        model_dir: Path,
        text: str,
        reference_audio: str,
        output_path: Path,
    ) -> TrainResult:
        """Síntesis de prueba con el modelo fine-tuned."""
        log = []

        try:
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts
            import torch
            import numpy as np
        except ImportError as e:
            return False, [f"[ERROR] Dependencia no disponible: {e}",
                           "  pip install TTS torch soundfile"]

        # Mismo parche que en finetune_xtts(): sin él, XTTS carga el audio de
        # referencia vía torchaudio/torchcodec, que en Windows falla si no hay
        # una build de FFmpeg compatible instalada (ver _patch_xtts_audio_loading).
        _patch_xtts_audio_loading()

        try:
            model_dir = Path(model_dir)
            config_candidates = list(model_dir.rglob("config.json"))
            if not config_candidates:
                return False, [f"[ERROR] config.json no encontrado en {model_dir}"]

            # Puede haber config.json de varios entrenamientos anteriores bajo
            # model_dir (Coqui crea una subcarpeta nueva por cada ejecución);
            # usar el más reciente en vez del primero que devuelva rglob.
            config_path = max(config_candidates, key=lambda p: p.stat().st_mtime)
            run_dir = config_path.parent
            log.append(f"Config: {config_path}")

            cfg = XttsConfig()
            cfg.load_json(str(config_path))

            # El checkpoint entrenado vive en run_dir, no en model_dir: Coqui
            # guarda cada ejecución como best_model_*.pth / checkpoint_*.pth,
            # nunca como "model.pth" literal (que es lo que asume
            # load_checkpoint si no se le pasa checkpoint_path explícito).
            checkpoints = sorted(run_dir.glob("best_model_*.pth"), key=lambda p: p.stat().st_mtime)
            if not checkpoints:
                checkpoints = sorted(run_dir.glob("checkpoint_*.pth"), key=lambda p: p.stat().st_mtime)
            if not checkpoints:
                return False, [f"[ERROR] No se encontró ningún checkpoint (best_model_*.pth / checkpoint_*.pth) en {run_dir}"]
            checkpoint_path = checkpoints[-1]
            log.append(f"Checkpoint: {checkpoint_path}")

            # vocab.json solo está en run_dir si el entrenamiento se hizo con
            # la copia automática (ver finetune_xtts); si no, cae a la ruta
            # original del checkpoint base grabada en config.json, que puede
            # no existir si el modelo se movió a otra máquina.
            vocab_path = run_dir / "vocab.json"
            if not vocab_path.is_file():
                fallback = Path(cfg.model_args.tokenizer_file)
                if fallback.is_file():
                    vocab_path = fallback
                else:
                    return False, [
                        f"[ERROR] vocab.json no encontrado en {run_dir} ni en la ruta "
                        f"original del checkpoint base ({fallback}).",
                        f"  Copia manualmente vocab.json del checkpoint base de XTTS-v2 a {run_dir}.",
                    ]

            model = Xtts.init_from_config(cfg)
            log.append(f"Cargando modelo desde {run_dir}...")
            model.load_checkpoint(
                cfg,
                checkpoint_path=str(checkpoint_path),
                vocab_path=str(vocab_path),
                eval=True,
            )

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = model.to(device)
            log.append(f"Dispositivo: {device}")

            log.append("Sintetizando...")
            outputs = model.synthesize(
                text, cfg,
                speaker_wav=str(reference_audio),
                language="es",
            )

            wav = np.array(outputs["wav"])

            try:
                import soundfile as sf
                sf.write(str(output_path), wav, 24000)
            except ImportError:
                import scipy.io.wavfile as scipy_wav
                scipy_wav.write(str(output_path), 24000, (wav * 32767).astype("int16"))

            log.append(f"[OK] Audio generado: {output_path}")
            return True, log

        except Exception as e:
            log.append(f"[ERROR] {e}")
            return False, log

    @staticmethod
    def zero_shot_synthesize(
        text: str,
        reference_audio: str,
        output_path: Path,
    ) -> TrainResult:
        """
        Síntesis zero-shot con el modelo base XTTS v2 preentrenado.
        No requiere entrenamiento previo: clona la voz del audio de referencia.
        Primera ejecución: descarga ~1.8 GB del modelo de HuggingFace.
        """
        log = []
        try:
            from TTS.api import TTS
        except ImportError as _e:
            return False, [
                f"[ERROR] CoquiTTS no disponible: {_e}",
                "  Python ≤3.11 : pip install TTS",
                "  Python  3.12 : pip install git+https://github.com/idiap/coqui-ai-TTS",
                "  PyTorch CPU  : pip install torch==2.7.1+cpu torchaudio==2.7.1+cpu "
                "--index-url https://download.pytorch.org/whl/cpu",
                "  Después reinicia la app Gradio.",
            ]

        # Ver _patch_xtts_audio_loading(): evita que la carga del audio de
        # referencia dependa de torchaudio/torchcodec (roto sin FFmpeg
        # compatible en Windows).
        _patch_xtts_audio_loading()

        try:
            log.append("Cargando modelo base XTTS v2...")
            log.append("(Primera vez: descarga ~1.8 GB — espera unos minutos)")
            tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
            device = "cuda" if _has_cuda() else "cpu"
            tts = tts.to(device)
            log.append(f"Dispositivo: {device}")
            log.append("Sintetizando...")
            tts.tts_to_file(
                text=text,
                speaker_wav=str(reference_audio),
                language="es",
                file_path=str(output_path),
            )
            log.append(f"[OK] Audio generado: {output_path}")
            return True, log
        except Exception as e:
            log.append(f"[ERROR] {e}")
            return False, log

    @staticmethod
    def train_remote(config: Dict[str, Any], api_base: str) -> TrainResult:
        log = [
            f"[STUB] Entrenamiento remoto XTTS v2 — servidor: {api_base}",
            "[STUB] Endpoint pendiente: POST /train/xtts",
            f"       Train : {config.get('train_csv')}",
            f"       Eval  : {config.get('eval_csv')}",
            f"       Salida: {config.get('output_dir')}",
        ]
        return False, log


# ---------------------------------------------------------------------------
# Piper
# ---------------------------------------------------------------------------

class PiperTrainer:
    """
    Entrenamiento de Piper TTS (VITS).

    Pipeline:
      1. piper_train.preprocess  — fonemiza con espeak-ng + mel spectrograms
      2. piper_train             — entrenamiento VITS
      3. export_onnx()           — convierte el checkpoint a .onnx (inferencia)
    """

    @staticmethod
    def required_format() -> str:
        return "piper"

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> bool:
        return all(k in config for k in {"train_csv", "output_dir"})

    @staticmethod
    def train_local(config: Dict[str, Any], progress_callback=None) -> TrainResult:
        log = []

        def L(msg):
            log.append(msg)
            if progress_callback:
                progress_callback("\n".join(log))

        L("Iniciando entrenamiento Piper (VITS)...")

        try:
            _lock = training_lock()
            _lock.__enter__()
        except TrainingInProgressError as e:
            L(f"[ERROR] {e}")
            return False, log

        try:
            train_csv  = Path(config["train_csv"])
            output_dir = Path(config["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            dataset_dir = train_csv.parent

            L(f"Train CSV : {train_csv}")
            L(f"WAVs      : {dataset_dir / 'wavs'}")
            L(f"Salida    : {output_dir}")

            # Verificar piper_train
            check = subprocess.run(
                [sys.executable, "-m", "piper_train", "--help"],
                capture_output=True,
            )
            if check.returncode != 0:
                L("[ERROR] piper_train no encontrado.")
                L("  pip install piper-train")
                L("  También necesitas espeak-ng instalado en el sistema.")
                return False, log

            # Paso 1: Preprocesar
            preprocessed_dir = output_dir / "preprocessed"
            preprocess_cmd = [
                sys.executable, "-m", "piper_train.preprocess",
                "--language",   "es",
                "--input-dir",  str(dataset_dir),
                "--output-dir", str(preprocessed_dir),
                "--dataset",    "ljspeech",
            ]
            L(f"Paso 1/2 — Preprocesando (fonemización)...")
            L(f"  {' '.join(preprocess_cmd)}")
            L("─" * 50)

            try:
                proc = subprocess.Popen(
                    preprocess_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in proc.stdout:
                    L(line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    L(f"[ERROR] Preprocesado terminó con código {proc.returncode}")
                    return False, log
            except Exception as e:
                L(f"[ERROR] {e}")
                return False, log

            L("Preprocesado completado.")

            # Paso 2: Entrenamiento VITS
            es_dir = preprocessed_dir / "es"
            quality = config.get("quality", "medium")
            epochs  = config.get("epochs", 6000)

            train_cmd = [
                sys.executable, "-m", "piper_train",
                "--dataset-dir",       str(es_dir),
                "--output-dir",        str(output_dir),
                "--quality",           quality,
                "--checkpoint-epochs", str(max(100, int(epochs) // 10)),
                "--max-epochs",        str(epochs),
            ]
            L(f"Paso 2/2 — Entrenando VITS ({quality}, {epochs} épocas)...")
            L(f"  {' '.join(train_cmd)}")
            L("─" * 50)

            try:
                proc = subprocess.Popen(
                    train_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in proc.stdout:
                    L(line.rstrip())
                proc.wait()

                if proc.returncode == 0:
                    L(f"[OK] Checkpoints en: {output_dir}")
                    L("Usa 'Exportar a ONNX' para obtener el modelo final.")
                    return True, log
                else:
                    L(f"[ERROR] piper_train terminó con código {proc.returncode}")
                    return False, log

            except Exception as e:
                L(f"[ERROR] {e}")
                return False, log
        finally:
            _lock.__exit__(None, None, None)

    @staticmethod
    def export_onnx(model_dir: Path) -> Tuple[bool, List[str], Optional[Path]]:
        """Convierte el checkpoint más reciente a .onnx para inferencia."""
        log = []
        model_dir = Path(model_dir)

        # Buscar el checkpoint más reciente (last.ckpt o el más nuevo)
        ckpts = sorted(model_dir.rglob("*.ckpt"), key=lambda p: p.stat().st_mtime)
        if not ckpts:
            return False, [f"[ERROR] No hay checkpoints en {model_dir}"], None

        # Preferir last.ckpt si existe
        last = model_dir / "last.ckpt"
        latest = last if last.exists() else ckpts[-1]
        onnx_path = model_dir / "model.onnx"

        log.append(f"Checkpoint: {latest.name}")
        log.append(f"Destino   : {onnx_path}")

        cmd = [
            sys.executable, "-m", "piper_train.export_onnx",
            "--checkpoint", str(latest),
            "--output",     str(onnx_path),
        ]
        log.append(f"Ejecutando: {' '.join(cmd)}")

        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            log.append(f"[OK] ONNX exportado: {onnx_path}")
            return True, log, onnx_path
        else:
            log.append(f"[ERROR] {proc.stderr or proc.stdout}")
            return False, log, None

    @staticmethod
    def synthesize(onnx_path: Path, text: str, output_path: Path) -> TrainResult:
        """Síntesis con modelo Piper ONNX."""
        log = []
        onnx_path = Path(onnx_path)

        if not onnx_path.exists():
            return False, [f"[ERROR] Modelo ONNX no encontrado: {onnx_path}",
                           "  Usa primero 'Exportar a ONNX'."]

        try:
            result = subprocess.run(
                ["piper", "--model", str(onnx_path), "--output-file", str(output_path)],
                input=text,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode == 0:
                log.append(f"[OK] Audio generado: {output_path}")
                return True, log
            else:
                log.append(f"[ERROR] piper: {result.stderr}")
                return False, log

        except FileNotFoundError:
            log.append("[ERROR] 'piper' no encontrado en PATH.")
            log.append("  pip install piper-tts")
            return False, log
        except subprocess.TimeoutExpired:
            log.append("[ERROR] Tiempo de síntesis agotado (>60s).")
            return False, log
        except Exception as e:
            log.append(f"[ERROR] {e}")
            return False, log

    @staticmethod
    def train_remote(config: Dict[str, Any], api_base: str) -> TrainResult:
        log = [
            f"[STUB] Entrenamiento remoto Piper — servidor: {api_base}",
            "[STUB] Endpoint pendiente: POST /train/piper",
            f"       Train CSV : {config.get('train_csv')}",
            f"       Salida    : {config.get('output_dir')}",
            "",
            "[INFO] Pipeline manual:",
            "  1. python -m piper_train.preprocess --language es --input-dir <dir> --output-dir <prep>",
            "  2. python -m piper_train --dataset-dir <prep/es> --output-dir <out>",
            "  3. python -m piper_train.export_onnx --checkpoint <out/last.ckpt> --output model.onnx",
        ]
        return False, log


# ---------------------------------------------------------------------------
# F5-TTS
# ---------------------------------------------------------------------------

class F5Trainer:
    """Fine-tuning de F5-TTS. Requiere GPU ≥12 GB VRAM."""

    @staticmethod
    def required_format() -> str:
        return "f5"

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> bool:
        return all(k in config for k in {"train_csv", "output_dir"})

    @staticmethod
    def _generate_config_yaml(train_csv: Path, output_dir: Path) -> Path:
        data_dir = train_csv.parent
        cfg_path = data_dir / "config.yaml"
        cfg_path.write_text(
            "# F5-TTS fine-tuning config — generado automáticamente\n"
            f"exp_dir: {output_dir}\n"
            "epochs: 100\n"
            "batch_size: 4\n"
            "learning_rate: 1e-4\n"
            "grad_accumulation_steps: 1\n"
            "dataset:\n"
            f"  - list_data_path: {train_csv}\n"
            "    type: CustomDataset\n",
            encoding="utf-8",
        )
        return cfg_path

    @staticmethod
    def train_local(config: Dict[str, Any], progress_callback=None) -> TrainResult:
        return False, [
            "[INFO] F5-TTS requiere GPU con ≥12 GB VRAM.",
            "[INFO] Entrenamiento local no habilitado. Usa el modo Servidor.",
            f"       Dataset listo en: {config.get('train_csv')}",
        ]

    @staticmethod
    def synthesize(model_dir: Path, text: str, reference_audio: str, output_path: Path) -> TrainResult:
        return False, ["[INFO] Síntesis F5-TTS pendiente de implementación en local."]

    @staticmethod
    def train_remote(config: Dict[str, Any], api_base: str) -> TrainResult:
        train_csv  = Path(config.get("train_csv", "."))
        output_dir = Path(config.get("output_dir", "."))
        cfg_path = F5Trainer._generate_config_yaml(train_csv, output_dir)
        return False, [
            f"[STUB] Entrenamiento remoto F5-TTS — servidor: {api_base}",
            "[STUB] Endpoint pendiente: POST /train/f5",
            f"[INFO] config.yaml generado: {cfg_path}",
            f"       Train CSV : {train_csv}",
            f"       Salida    : {output_dir}",
            "",
            "[INFO] Para entrenar manualmente:",
            f"       f5-tts_train --config {cfg_path} --exp_dir {output_dir}",
        ]


# ---------------------------------------------------------------------------
# Registro de modelos
# ---------------------------------------------------------------------------

MODELS: Dict[str, Dict[str, Any]] = {
    "xtts": {
        "name":        "XTTS v2",
        "description": "Fine-tuning rápido con pocos datos, zero-shot con audio referencia",
        "trainer":     XTTSTrainer,
        "supports_local":    True,
        "supports_remote":   True,
        "supports_zero_shot": True,
        "recommended_min_duration_min": 30,
        "epoch_default": 10,
        "epoch_min": 1,
        "epoch_max": 500,
        "epoch_step": 1,
        "batch_default": 2,
        "lr_default": "5e-6",
    },
    "piper": {
        "name":        "Piper",
        "description": "Ligero, ideal para Raspberry Pi y edge devices",
        "trainer":     PiperTrainer,
        "supports_local":    True,
        "supports_remote":   True,
        "supports_zero_shot": False,
        "recommended_min_duration_min": 20,
        "epoch_default": 6000,
        "epoch_min": 1000,
        "epoch_max": 10000,
        "epoch_step": 100,
        "batch_default": 16,
        "lr_default": "1e-4",
    },
    "f5": {
        "name":        "F5-TTS",
        "description": "Última generación, requiere GPU ≥12 GB VRAM",
        "trainer":     F5Trainer,
        "supports_local":    False,
        "supports_remote":   True,
        "supports_zero_shot": True,
        "recommended_min_duration_min": 30,
        "epoch_default": 100,
        "epoch_min": 50,
        "epoch_max": 1000,
        "epoch_step": 10,
        "batch_default": 4,
        "lr_default": "1e-4",
    },
}


def get_available_models() -> Dict[str, Dict[str, Any]]:
    return MODELS


def get_trainer_for_model(model_code: str):
    if model_code not in MODELS:
        raise ValueError(f"Modelo '{model_code}' no registrado. Disponibles: {list(MODELS)}")
    return MODELS[model_code]["trainer"]
