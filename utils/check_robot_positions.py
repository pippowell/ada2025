"""
Flag camera frames that were not taken where they should have been, using the
robot position logs and the archive checksums of the ADA 2025 camera data.

Reads the per-plant camera archives as written by utils/download_dataset.py
(<data-dir>/<modality>/<modality>_<bed>_<plant>.zip; subfolders are searched
too) without extracting them: the frames_<sensor>.csv files are read from the
archives and the image checksums (CRC-32 and size) come from the archive index.

Per RGB, depth and thermal frame it sets:

- off_position: the frame was logged more than --max-offset metres (default
  0.05) from the plant's median position for that frame number over all runs.
  On the eight manually reviewed plants this reproduces the manual
  "Not Centered" label for 99-100 % of frames in both directions
  (see docs/data_notes.md).
- stalled: the image is byte-identical to images of more plants than normal
  frame sharing explains (the robot stopped and the same capture was saved for
  many plants). Neighbouring plants share some RGB and depth frames by design;
  thermal frames are never shared in normal runs. Thresholds: thermal >= 2
  plants, RGB >= 5, depth >= 9.

and per run: no_csv (frames_<sensor>.csv missing). It also lists the
hyperspectral runs dated outside the collection period (Aug 12 to Sep 22).

Output: analysis/dataset_issues/robot_position_flags.csv, one row per
plant/run/modality with at least one flag, and a summary on stdout.

Usage (from the repository root):
    python utils/check_robot_positions.py [--data-dir data] [--output ...]

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import csv
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

REPO = Path(__file__).resolve().parent.parent
POSITION_MODALITIES = ("rgb", "depth", "thermal")
CENTER_FRAME = {"rgb": 7, "depth": 7, "thermal": 2}
STALL_MIN_PLANTS = {"thermal": 2, "rgb": 5, "depth": 9}
COLLECTION = ("2025_08_12", "2025_09_22")
ARCHIVE_RE = re.compile(r"^(rgb|depth|thermal|hsi)_(W\d_[A-R]\d)\.zip$")


def read_archives(data_dir: Path):
    """Return (positions, images, runs) DataFrames from all camera archives under data_dir."""
    archives = sorted(p for p in data_dir.rglob("*.zip") if ARCHIVE_RE.match(p.name))
    if not archives:
        raise SystemExit(f"no camera archives (<modality>_<bed>_<plant>.zip) under {data_dir}")
    pos, img, runs = [], [], []
    for zp in tqdm(archives, desc="archives", unit="zip"):
        mod, plant = ARCHIVE_RE.match(zp.name).groups()
        with zipfile.ZipFile(zp) as z:
            csvs, counts = {}, {}
            for zi in z.infolist():
                parts = zi.filename.split("/")
                if len(parts) != 4:
                    continue
                _, date, hour, name = parts
                if name == f"frames_{mod}.csv":
                    csvs[(date, hour)] = zi.filename
                elif re.fullmatch(rf"{mod}_\d{{3}}\.(jpg|tif|jp2)", name):
                    img.append((mod, plant, date, hour, int(name[len(mod) + 1:len(mod) + 4]), zi.CRC, zi.file_size))
                    counts[(date, hour)] = counts.get((date, hour), 0) + 1
            if mod not in POSITION_MODALITIES:
                continue
            for (date, hour), n in counts.items():
                runs.append((mod, plant, date, hour, n, (date, hour) in csvs))
                if (date, hour) in csvs:
                    for r in csv.DictReader(io.StringIO(z.read(csvs[(date, hour)]).decode())):
                        pos.append((mod, plant, date, hour, int(r["frame_id"]),
                                    float(r["position_robot_x"]), float(r["position_robot_y"])))
    key = ["modality", "plant", "date", "hour"]
    return (pd.DataFrame(pos, columns=key + ["frame", "x", "y"]),
            pd.DataFrame(img, columns=key + ["frame", "crc", "size"]),
            pd.DataFrame(runs, columns=key + ["n_images", "has_csv"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--output", default=str(REPO / "analysis" / "dataset_issues" / "robot_position_flags.csv"))
    ap.add_argument("--max-offset", type=float, default=0.05, help="metres (default 0.05)")
    args = ap.parse_args()
    key = ["modality", "plant", "date", "hour"]

    pos, img, runs = read_archives(Path(args.data_dir))

    # off_position: distance to the plant's median position for that frame number
    g = pos.groupby(["modality", "plant", "frame"])
    pos["offset_m"] = np.hypot(pos.x - g.x.transform("median"), pos.y - g.y.transform("median"))
    pos["off_position"] = pos.offset_m > args.max_offset

    # stalled: identical content saved for more plants than normal sharing explains
    cam = img[img.modality.isin(POSITION_MODALITIES)].copy()
    cam["n_plants"] = cam.groupby(["modality", "crc", "size"]).plant.transform("nunique")
    cam["stalled"] = cam.n_plants >= cam.modality.map(STALL_MIN_PLANTS)
    frames = pos.merge(cam[key + ["frame", "n_plants", "stalled"]], on=key + ["frame"], how="right")
    frames["off_position"] = frames.off_position.fillna(False).astype(bool)

    def fmt(s):
        return " ".join(f"{f:03d}" for f in sorted(s))

    per_run = frames.groupby(key).apply(lambda s: pd.Series({
        "off_position_frames": fmt(s.frame[s.off_position]),
        "center_off_position": bool(s.off_position[s.frame == CENTER_FRAME[s.name[0]]].any()),
        "stalled_frames": fmt(s.frame[s.stalled]),
        "max_offset_m": round(float(s.offset_m.max()), 3) if s.offset_m.notna().any() else np.nan,
        "max_plants_sharing_image": int(s.n_plants.max()),
    }), include_groups=False).reset_index()
    per_run = per_run.merge(runs, on=key)
    per_run["no_csv"] = ~per_run.has_csv
    flagged = per_run[(per_run.off_position_frames != "") | (per_run.stalled_frames != "") | per_run.no_csv]
    cols = key + ["n_images", "no_csv", "off_position_frames", "center_off_position", "max_offset_m",
                  "stalled_frames", "max_plants_sharing_image"]
    flagged = flagged[cols].sort_values(key)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    flagged.to_csv(args.output, index=False)

    # summary
    print(f"\nwrote {len(flagged):,} flagged runs to {args.output}\n")
    print("modality  runs    no_csv  runs_off  center_off  frames_off  frames  stalled_runs  stalled_frames")
    for m in POSITION_MODALITIES:
        r, f = per_run[per_run.modality == m], frames[frames.modality == m]
        print(f"{m:8s} {len(r):6d} {int(r.no_csv.sum()):8d} {int((r.off_position_frames != '').sum()):9d}"
              f" {int(r.center_off_position.sum()):11d} {int(f.off_position.sum()):11d} {len(f):8d}"
              f" {int((r.stalled_frames != '').sum()):13d} {int(f.stalled.sum()):15d}")
    print("\nno frames_<sensor>.csv:")
    nc = per_run[per_run.no_csv].assign(bed=lambda d: d.plant.str[:2])
    for (m, d, h, b), s in nc.groupby(["modality", "date", "hour", "bed"]):
        print(f"  {m:8s} {d} {h} h {b}: {len(s)} plants")
    print("\nstall events (runs where one image was saved for >= 10 plants of a bed):")
    st = frames[frames.stalled & (frames.n_plants >= 10)].assign(bed=lambda d: d.plant.str[:2])
    for (m, d, h, b), s in st.groupby(["modality", "date", "hour", "bed"]):
        print(f"  {m:8s} {d} {h} h {b}: {s.plant.nunique()} plants")
    hsi = img[img.modality == "hsi"]
    out = hsi[(hsi.date < COLLECTION[0]) | (hsi.date > COLLECTION[1])]
    print(f"\nhyperspectral images dated outside {COLLECTION[0]}..{COLLECTION[1]}: {len(out):,} "
          f"(of {len(hsi):,}); runs:")
    for (d, h), s in out.groupby(["date", "hour"]):
        print(f"  {d} {h} h: {s.plant.nunique()} plants")


if __name__ == "__main__":
    main()
