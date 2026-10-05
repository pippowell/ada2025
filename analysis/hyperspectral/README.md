# Hyperspectral spectral-index pipeline

This folder contains the pipeline that turns the raw HSI cubes of the ADA 2025
dataset (https://doi.org/10.26249/FK2/DMDLTE) into per-bed spectral vegetation
indices — the data behind panels (b) NDVI and (c) SIPI of the paper's results
figure and the hyperspectral statistics in `analysis/combined/run_stats.py`.

It is a self-contained adaptation of the hyperspectral-index code from the
hsi-plant-segmentation repository.  The only external data needed are the
unzipped HSI cubes in `data/hsi/` (see the repository README, "Preparing the
`data` Folder").  Everything else lives in this folder:

- `calc_spectral_index.py` — the pipeline script
- `generate_plant_masks.py` — one-off generation of plant-mask images into the
  dataset folders (see "Precomputed masks" below)
- `models/model.pt2` — TorchExport plant-segmentation model used for
  plant-masked index means (torch.export.load). A 1D-CNN spectral encoder
  compresses each pixel's spectrum into a 32-channel embedding, and a U-Net
  decoder produces a pixel-wise plant/background probability mask (about
  3.5M parameters). It was trained with a combined binary cross-entropy and
  Dice loss on 234 manually annotated masks from ten plants in beds W1 and W2,
  the same plants used in the exemplary analysis. It is included to reproduce
  that analysis, not as a general-purpose plant segmentation model.
  Pixels with a probability of at least 0.5 (`--mask-threshold`) count as plant.

The `hyperio` library (used for reading the HSI cubes and computing the
indices) is installed as a git dependency from
https://github.com/unklar/hyperio — it is pinned in
`environment/requirements.txt` / `environment.yml` rather than vendored here.

## Usage

The defaults cover the paper's recipe: plants
`W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7` (the
paper's five plants per bed in W1 and W2), 3 pm run only, Sep 1-14 window,
plant-masked means with a spectral window of 5, all 21 indices, and
Savitzky-Golay smoothing (`--no-savgol` disables it).  The shipped
`data/results_w5_mean_15_sa.xlsx` additionally uses the flat-field
correction, so add `--dark-reference` to reproduce it (see below).

```bash
cd analysis/hyperspectral

# Reproduce the shipped data/results_w5_mean_15_sa.xlsx
python calc_spectral_index.py --dark-reference

# One index only
python calc_spectral_index.py --index ndvi

# The three paper indices (NDVI and SIPI panels, plus CI Red Edge used in the
# statistics), writing into the data folder where run_stats.py and
# paper/make_results_figure.py expect the workbook
python calc_spectral_index.py --dark-reference --index ndvi,ci_rededge,sipi \
    --csv ../../data/results_w5_mean_15_sa.csv \
    --xlsx ../../data/results_w5_mean_15_sa.xlsx \
    --output processed/results_w5_mean_15_sa.png

# Whole image (no model masking), all four recording hours
python calc_spectral_index.py --no-mask --time-ids 06,09,12,15,18 \
    --start-date 2025_08_12 --end-date 2025_09_22

# Per-date+hour grouping instead of per-date
python calc_spectral_index.py --group-by hour --index ndvi,evi,sipi
```

## Flat correction (`--dark-reference`)

Raw DNs are normalized against the gray calibration reference stored per
image (`R = DN / (W·m)`, with the stored scale factor m = 2.0 mapping the
50 % gray panel to R ≈ 0.5).  Passing the black (dark) reference applies the
standard flat-field correction `R = (raw − dark) / (white − dark)` instead of
that gray-only normalization.  It reads `black_reference.npy` in this folder
by default when the flag is given without a path:

```bash
python calc_spectral_index.py --dark-reference              # black_reference.npy
python calc_spectral_index.py --dark-reference /path/black.npy
python generate_plant_masks.py --dark-reference
```

Off unless the flag is passed.  The shipped `results_w5_mean_15_sa.xlsx` and
the paper's results were computed with it (the paper's calibration formula
includes the dark frame).  Because the black reference is ~0 at
550/670/800 nm, NDVI barely moves (daily bed means within 4e-4 of the
uncorrected run; the corrected cube also feeds the segmentation model, so the
plant mask changes slightly too); indices using other bands shift more (CI
Red Edge up to 0.006, MTCI/CRI2/ARI up to 0.025).  The paper's conclusions
are the same with and without it.  Implemented by hyperio
(`HSI.read(..., dark_reference=...)`).

## Precomputed masks (`--use-masks`)

By default the pipeline runs the segmentation model on every image to derive
the plant mask.  Alternatively, you can precompute the masks **once per
dataset** and reuse them across runs (any index set, window, or grouping),
which also lets the index pipeline run on a machine without torch:

```bash
# 1. Generate hsi_000_mask.png next to every hsi_000.jp2 in data/hsi/
python generate_plant_masks.py

# 2. Use those masks instead of re-running the model
python calc_spectral_index.py --use-masks
```

`generate_plant_masks.py` mirrors the index script's defaults (the same
`--plant-ids` full `<bed>_<plant>` list, 3 pm run, Sep 1-14), skips masks
that already exist (pass `--overwrite` to regenerate), and writes each soft
mask as an 8-bit grayscale PNG
(`hsi_000_mask.png`) next to its source image.  With `--use-masks` the index
script loads that PNG and applies the same `--mask-threshold`, so results are
identical to the in-model run up to mask-quantization noise (<1e-4).  Masks
depend on whether the cube was flat-field corrected, so generate them with
`--dark-reference` too when reproducing the shipped workbook with
`--use-masks`.  Mask images live in the
git-ignored `data/` folder and are not part of the repository.

`--use-masks` and `--no-mask` are mutually exclusive.  `--mask-threshold`
(default 0.5) controls how aggressive the plant mask is in both masking modes.

Outputs land in `processed/` by default (`spectral_indices_by_bed.csv`,
`.xlsx`, and `spectral_indices_by_bed.png`); pass `--csv`/`--xlsx`/`--output`
explicitly to write elsewhere.  The CSV/Excel columns
(`index`, `bed_id`, `group`, `mean`, `median`, `std`, `min`, `max`, `q25`,
`q75`, `iqr`, `n_images`) match the shipped
`data/results_w5_mean_15_sa.xlsx`, so a regenerated workbook can be consumed
by `paper/make_results_figure.py` and `analysis/combined/run_stats.py`.

## Dataset layout

Only `data/hsi/` is needed (extracted with `utils/unzip_data.py`; hour
directories are zero-padded):

```
data/hsi/<bed>_<plant>/<YYYY_MM_DD>/<HH>/hsi_*.jp2
```

## Available indices

`ndvi`, `evi`, `savi`, `msavi`, `mcari`, `pri`, `ndwi`, `red_edge_ndvi`,
`green_edge_ndvi`, `ndre`, `msi`, `psri`, `ari`, `ci_rededge`, `ccci`,
`sipi`, `tcari_osavi`, `gndvi`, `datt1`, `cri2`, `mtci`.

## Environment

Run from the repository's `ada2025` environment.  The pipeline needs `torch`
(plant-masking model) and `hyperio` (installed from
https://github.com/unklar/hyperio); both are declared in
`environment/requirements.txt` and `environment.yml` (the latter via its `pip`
section).  `hyperio` pulls `rasterio`, `spectral`, `scikit-image`, `scipy`,
`tifffile`, and `Pillow` transitively.

## Reproducing the shipped results file

The shipped `data/results_w5_mean_15_sa.xlsx` is what the script produces
with its defaults plus the flat-field correction (window 5, mean aggregation,
3 pm run, plant-masked means, all 21 indices, Savitzky-Golay smoothing,
Sep 1-14, `black_reference.npy`):

```
python calc_spectral_index.py --dark-reference
```

The shipped file was generated on CPU.  Model inference on different hardware
(e.g. CUDA) can differ slightly; an earlier no-dark workbook made on GPU and
its CPU regeneration agreed to <1e-4 in every statistic column.  Pass
`--start-date`/`--end-date` to restrict the window.

## Notes

- `--group-by hour` keeps intra-day variation (per date+hour), `--group-by
  day` aggregates over the day.
- All indices are computed in a single pass per image (the segmentation model
  runs once per image).
- This folder was developed with assistance from Claude (Anthropic) via
  Claude Code.