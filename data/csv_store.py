import csv
import random
from pathlib import Path

from config import LANG, TRAIN_RATIO, HEADER_LOCAL, HEADER_GLOBAL

def load_existing(csv_path) -> set:
    done = set()
    if not Path(csv_path).exists():
        return done
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="|"):
            if row and row[0] != "audio":
                done.add(row[0])
    return done


def migrate_csv(csv_path, new_header: list):
    p = Path(csv_path)
    if not p.exists():
        return
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))
    if not rows or len(rows[0]) >= len(new_header):
        return
    print(f" [migracion] {p.name}: {len(rows[0])} -> {len(new_header)} columnas")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(new_header)
        for row in rows[1:]:
            padded = row + [""] * (len(new_header) - len(row))
            w.writerow(padded[:len(new_header)])


def split_train_eval(csv_path, train_path, eval_path, header: list, write_header: bool = True):
    if not Path(csv_path).exists():
        return 0, 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter="|") if r and r[0] != "audio"]
    random.seed(42)
    random.shuffle(rows)
    cut = int(len(rows) * TRAIN_RATIO)
    for path, data in [(train_path, rows[:cut]), (eval_path, rows[cut:])]:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="|")
            if write_header and header:
                w.writerow(header)
            w.writerows(data)
    return len(rows[:cut]), len(rows[cut:])


def append_to_global(global_csv, local_csv, municipio: str, provincia: str):
    if not Path(local_csv).exists():
        return
    with open(local_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter="|") if r[0] != "audio"]
    new_rows = [
        [row[0], row[1], row[2] if len(row) > 2 else LANG, municipio, provincia]
        for row in rows
    ]
    global_exists = Path(global_csv).exists()
    with open(global_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|")
        if not global_exists:
            w.writerow(HEADER_GLOBAL)
        w.writerows(new_rows)


def rotate_backup(path: Path):
    if not path.exists():
        return
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        bak.unlink()
    import shutil
    shutil.copy2(path, bak)