from pathlib import Path


def cargar_modelo_whisper(whisper_model: str, cache_dir: Path):
    import whisper as wmod
    print(f" . Cargando modelo Whisper '{whisper_model}' desde {cache_dir}...")
    model = wmod.load_model(whisper_model, download_root=str(cache_dir))
    print(" . Modelo cargado.")
    return model


def transcribir(model, wav_path: Path, prompt: str = "") -> str:
    kwargs = dict(language="es", task="transcribe", fp16=False)
    if prompt:
        kwargs["initial_prompt"] = prompt
    res = model.transcribe(str(wav_path), **kwargs)
    return res["text"].strip()