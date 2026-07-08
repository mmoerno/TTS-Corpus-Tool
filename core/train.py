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
import subprocess
import sys
import os

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


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
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts
            from TTS.trainer import Trainer, TrainerArgs
        except ImportError as _e:
            L(f"[ERROR] CoquiTTS no disponible: {_e}")
            L("  Python ≤3.11 : pip install TTS")
            L("  Python  3.12 : pip install git+https://github.com/idiap/coqui-ai-TTS")
            L("  Reinicia la app Gradio tras instalar.")
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

            # ── Detección de dispositivo y optimización CPU ──────────────────
            try:
                import torch as _torch
                device = "cuda" if _has_cuda() else "cpu"
                L(f"Dispositivo : {device.upper()}")
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

            xtts_config = XttsConfig()
            xtts_config.epochs     = int(config.get("epochs", 10))
            xtts_config.batch_size = int(config.get("batch_size", 2))
            xtts_config.lr         = float(config.get("lr", "5e-6"))
            xtts_config.output_path = str(output_dir)
            xtts_config.datasets   = [dataset_config]

            # Sin GPU: deshabilitar mixed precision (requiere CUDA; sin él puede
            # lanzar RuntimeError: "Expected all tensors to be on the same device")
            xtts_config.mixed_precision = _has_cuda()

            # DataLoader workers: en Windows multiprocessing puede dar problemas
            # con >0 workers dentro de Gradio; 4 es el máximo seguro probado.
            try:
                n_loader = 4 if sys.platform == "win32" else min(8, (os.cpu_count() or 4) // 3)
                xtts_config.num_loader_workers = n_loader
                xtts_config.num_eval_loader_workers = max(1, n_loader // 2)
                L(f"DataLoader  : {n_loader} workers train / {max(1, n_loader // 2)} eval")
            except AttributeError:
                pass  # El fork de Idiap puede no exponer estos atributos

            model = Xtts.init_from_config(xtts_config)

            checkpoint_dir = config.get("xtts_checkpoint_dir")
            if checkpoint_dir:
                checkpoint_dir = Path(checkpoint_dir)
                config_json = checkpoint_dir / "config.json"
                if config_json.exists():
                    xtts_config.load_json(str(config_json))
                try:
                    model.load_checkpoint(
                        xtts_config,
                        checkpoint_dir=str(checkpoint_dir),
                        eval=False,
                    )
                    L(f"Checkpoint base cargado: {checkpoint_dir}")
                    L("Iniciando fine-tuning...")
                except Exception as e:
                    L(f"[ADVERTENCIA] No se pudo cargar checkpoint: {e}")
                    L("[INFO] Entrenando desde cero.")
            else:
                L("[ADVERTENCIA] 'xtts_checkpoint_dir' no especificado.")
                L("[INFO] Para fine-tuning descarga coqui/XTTS-v2 de HuggingFace.")
                L("[INFO] Entrenando desde cero (requiere más datos).")

            trainer = Trainer(
                TrainerArgs(output_path=str(output_dir)),
                xtts_config,
                output_path=str(output_dir),
                model=model,
            )

            L("Entrenando...")
            trainer.fit()
            L(f"[OK] Modelo guardado en: {output_dir}")
            return True, log

        except Exception as e:
            L(f"[ERROR] {e}")
            return False, log

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

        try:
            model_dir = Path(model_dir)
            config_candidates = list(model_dir.rglob("config.json"))
            if not config_candidates:
                return False, [f"[ERROR] config.json no encontrado en {model_dir}"]

            config_path = config_candidates[0]
            log.append(f"Config: {config_path}")

            cfg = XttsConfig()
            cfg.load_json(str(config_path))

            model = Xtts.init_from_config(cfg)
            log.append(f"Cargando modelo desde {model_dir}...")
            model.load_checkpoint(cfg, checkpoint_dir=str(model_dir), eval=True)

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
