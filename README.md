# ADA 2025 - A Multimodal Dataset for Water-Deficit Stress Detection in Sugar Beets

This is the companion repository of the ADA 2025 dataset ([doi:10.26249/FK2/DMDLTE](https://doi.org/10.26249/FK2/DMDLTE)). It contains:

- tools to download, unpack and check the dataset (`utils/`)
- documentation of the file formats, the camera calibration and the known data issues (`docs/`)
- code for working with the data, e.g. fusing depth, RGB and thermal images into 3D point clouds (`analysis/combined/`)
- the code behind the exemplary analyses in the ADA 2025 dataset paper, with instructions for reproducing its results (`analysis/`, `paper/`)

## The Dataset

ADA 2025 is a multimodal dataset of 216 greenhouse-grown sugar beet plants (*Beta vulgaris* ssp. *vulgaris* var. *altissima*, variety CALLEDIA KWS Öko) under healthy and water-deficit conditions. It combines hyperspectral, thermal, RGB and depth imagery with environmental sensor data, leaf-thickness (turgor) and relative water content (RWC) ground truth, and manual plant observations. The camera data alone is over 600 GB.

### Experiment

- **Plants and beds.** The plants were seeded on August 4, 2025, in individual pots, and arranged in a checkerboard pattern in three beds of 72 plants each (`W1`, `W2`, `W3`; 2 m x 1 m). Plants are named by bed and grid position, e.g. `W2_J5` (row `A`-`R`, column `1`-`8`).
- **Treatment.** `W1` is the control bed and was fully watered throughout. Irrigation of the two water-deficit beds `W2` and `W3` was turned off on September 3 (last watering at 11:00, at about BBCH 14) and resumed on September 15 to assess recovery.
- **Acquisition.** The ADA robot (a modified FarmBot Genesis carrying all four cameras) flew over all three beds five times a day, at 06, 09, 12, 15 and 18 h, from August 12 (emergence) to September 22, 2025, taking top-down images of every plant. Failed runs were often re-run shortly afterwards.
- **Ground truth and observations.** 21 leaf-thickness clips (9 each in `W2` and `W3`, 3 in `W1`) from September 2; destructive RWC samples from `W3` during the stress phase; BBCH estimates and symptom notes (including aphids and powdery and downy mildew) for every plant on seven days from September 2; leaf scans of the RWC samples; and a photo of every plant on the last day.

### Contents

| Modality | Sensor | Per plant and run | Total files | Archive size |
|---|---|---|---|---|
| RGB | Logitech C920, 1920 x 1080 | 15 images | 761,400 | 194.5 GB |
| Depth | Pico Flexx, 224 x 171 (depth + NIR intensity) | 15 images | 694,440 | 104.5 GB |
| Thermal | FLIR A70, 640 x 480, °C | 5 images | 231,373 | 114.1 GB |
| Hyperspectral | Resonon Pika L, 300 bands, ~384-1025 nm | 1 push-broom scan | 50,544 | 205.3 GB |

| Sensor data | Interval | Readings |
|---|---|---|
| Air temperature, humidity, pressure (2 x BME280) | 2 min | 59,648 |
| Leaf thickness / turgor (21 AgriHouse clips) | 2 min | 294,247 |
| Soil temperature and moisture (4 sensors, `W2`; not usable, see below) | 2 min | 119,292 |
| PAR (Driesen sensor, raw mV) | 15 min | 4,484 |

The totals cover August 12 to September 22, all scheduled runs and all re-runs. The dataset card links to five sub-datasets on osnaData:

| Sub-dataset | DOI | Files (size) |
|---|---|---|
| Hyperspectral | [10.26249/FK2/PK9YDR](https://doi.org/10.26249/FK2/PK9YDR) | `hsi_W1.zip` (57.9 GB), `hsi_W2.zip` (73.8 GB), `hsi_W3.zip` (73.6 GB) |
| Thermal | [10.26249/FK2/ASL0BV](https://doi.org/10.26249/FK2/ASL0BV) | `thermal_W1.zip` (36.1 GB), `thermal_W2.zip` (39.8 GB), `thermal_W3.zip` (38.2 GB) |
| RGB | [10.26249/FK2/FGLG94](https://doi.org/10.26249/FK2/FGLG94) | `rgb_W1.zip` (55.1 GB), `rgb_W2.zip` (69.9 GB), `rgb_W3.zip` (69.5 GB) |
| Depth | [10.26249/FK2/SMHP1Y](https://doi.org/10.26249/FK2/SMHP1Y) | `depth_W1.zip` (31.7 GB), `depth_W2.zip` (36.6 GB), `depth_W3.zip` (36.2 GB) |
| Environmental | [10.26249/FK2/C0HOBN](https://doi.org/10.26249/FK2/C0HOBN) | `air_readings.parquet`, `leaf_readings.parquet`, `soil_readings.parquet`, `metadata_rwc_par_en.xlsx` (and the German original `metadata_rwc_par.xlsx`), `Leaf Scans.zip`, `Plant Images.zip` |

The 12 camera archives total 618.4 GB.

Once extracted, the camera data is organised as `<modality>/<bed>_<plant>/<YYYY_MM_DD>/<HH>/`, e.g. `thermal/W1_A2/2025_09_01/15/thermal_000.tif`. Every file and column is described in [docs/data_files.md](docs/data_files.md).

### Documentation

- [docs/data_files.md](docs/data_files.md): folder layout, image formats, the per-frame robot positions (`frames_<sensor>.csv`), the hyperspectral metadata (`hsi_000.json`), the sensor parquet files and the sheets of the metadata workbook.
- [docs/data_notes.md](docs/data_notes.md): known issues: missing runs by date, bed and modality, image quality, robot position data, quality labels.
- [docs/camera_calibration.md](docs/camera_calibration.md): camera intrinsics and extrinsics, coordinate frames, and how to fuse depth, RGB and thermal images into a 3D point cloud.

## Getting Started

### Environment

The `environment/` folder contains the Python environment used for all code in this repository.

**Conda (recommended):**
```
conda env create -f environment/environment.yml
conda activate ada2025
```

**pip:**
```
pip install -r environment/requirements.txt
```

All commands below are run from the repository root unless noted otherwise.

### Downloading and unpacking

> **Check your free disk space first.** The camera archives are large: 30-75 GB each, 618 GB for all twelve, 401 GB for the eight needed to replicate the paper, and each archive holds a whole bed, so there is no smaller download for a few plants. Most laptops do not have room for this in the repository folder. Put the data on an external or secondary drive with `--data-dir` (below), and run `--list-only` first to see what a selection costs. Once the plants you need are extracted, the archives can be deleted; the analysis scripts only read the extracted files.

**`utils/download_dataset.py`** downloads the sub-datasets from osnaData and verifies their MD5 checksums. Everything lands in this repo's `data/` folder by default: the camera archives in `data/<modality>/`, the environmental and metadata files directly in `data/`. Files already downloaded intact are skipped, so an interrupted download resumes when you re-run the same command.

- `--modalities` selects sub-datasets (`hsi thermal rgb depth environmental`; default: all)
- `--beds` selects which beds' camera archives to download (`W1 W2 W3`; default: all); the environmental sub-dataset is not split by bed
- `--paper` downloads exactly what is needed to replicate the paper results (see [Replicating the Paper Results](#replicating-the-paper-results))
- `--list-only` shows the selected files and sizes without downloading; `-y` skips the confirmation prompt
- `--data-dir <folder>` puts the data elsewhere, e.g. on an external drive (then pass the same `--data-dir` to the unzip script and every analysis script)

```
python utils/download_dataset.py --list-only                      # everything, about 620 GB
python utils/download_dataset.py --modalities environmental thermal --beds W1
```

**`utils/unzip_data.py`** extracts the archives next to themselves into the `data/<modality>/<bed>_<plant>/<date>/<hour>/` layout that all scripts read. It asks which modalities, beds and plants to extract (or takes `--modalities`, `--beds`, `--plants`, `-y`), so only what you need is unpacked. Re-running skips files that are already extracted. The archives can also be extracted by hand inside a `<modality>/` folder; their paths start at the plant folder.

```
python utils/unzip_data.py                                          # interactive
python utils/unzip_data.py --modalities thermal --beds W1 W2 --plants all -y
```

### Checking the data

**`utils/sensor_summary.py`** prints record counts, sensor counts, sampling interval and date range for each parquet file and the PAR logger sheet.

**`utils/camera_data_summary.py`** counts the extracted camera images per modality over the collection period (Aug 12 - Sep 22 by default, all runs including re-runs) and reports the share of planned camera data that is missing. With all four modalities and all 216 plants extracted, it reproduces the totals above and the 6.44 % missing figure.

```
python utils/sensor_summary.py
python utils/camera_data_summary.py
```

### 3D fusion of depth, RGB and thermal

The depth, RGB and thermal cameras are jointly calibrated; the parameters are in `analysis/combined/camera_calibration.py` and documented in [docs/camera_calibration.md](docs/camera_calibration.md).

- **`analysis/combined/minimal_example_sensor_fusion.py`** builds the point cloud for one plant step by step from the intrinsics, extrinsics and per-frame robot positions, and shows it coloured by NIR intensity, RGB and thermal. Start here to see the math.
- **`analysis/combined/3d_viewer.py`** is an interactive Open3D viewer for any plant, date and run: color the points by RGB, height, NIR intensity or thermal, toggle the sensor frustums, pick the colormap.

Both need the rgb, depth and thermal data of the plant extracted under `data/`.

```
python analysis/combined/minimal_example_sensor_fusion.py
python analysis/combined/3d_viewer.py                       # interactive selection
python analysis/combined/3d_viewer.py --plant-id W1_A2 --date 2025_09_01 --hour 15
```

## Replicating the Paper Results

The exemplary analyses in the paper use a small, fixed part of the dataset:

- **Beds `W1` (control) and `W2` (water deficit).** `W3` is not used.
- **Five plants per bed**: the four corners and the center of the grid, `A2, A8, J5, R1, R7`. The scripts use these by default (`--plant-ids` selects others), so extracting more plants does not change the results.
- **The 3 pm (15 h) run only.** One exception: bed `W2` has no 3 pm thermal images on 2025-09-11, so the thermal scripts fall back to the 16:00 re-run (any re-run within ±1 h counts; `--no-recovery` disables this). That reading keeps `hour` 15 and records `capture_hour` 16, with the air-temperature window taken from the 16:00 run.
- **Dates.** All models and correlations use September 3 (irrigation cutoff, day 0) to September 14 (the day before irrigation resumed). The inputs and the results figure cover September 1-14, so the two days before the cutoff show both beds behaving alike.

### 1. Get the data

You need the environmental sub-dataset (1.5 GB) and the `W1` and `W2` archives of all four camera modalities (eight archives, 400.9 GB). `--paper` downloads exactly these. Then extract only the ten analysis plants, which take about 26 GB (all dates and runs of those plants):

```
python utils/download_dataset.py --paper
python utils/unzip_data.py --modalities thermal hsi rgb depth --beds W1 W2 --plants A2 A8 J5 R1 R7 -y
```

Plan for about 430 GB free during download and extraction; after deleting the archives, the extracted plants and the environmental files need about 28 GB. With the data on another drive, add the same `--data-dir <folder>` to both commands above and to every analysis step below that reads the data (Steps 1, 3, 4, 6 and 7, and `make_example_figure.py`). Use an absolute path, since Step 3 and Step 7 are run from their own folders.

Before running the analyses, `data/` must contain the extracted camera data, the parquet files (e.g. `air_readings.parquet`) and the hyperspectral results workbook `results_w5_mean_15_sa.xlsx`. The workbook is tracked in this repository: it is the reference output of Step 3, and Step 4 and the results figure read it by that name. Step 3 can regenerate it on top of the tracked copy, but do not rename it.

### 2. Run the analysis pipeline

**Step 1 — `analysis/leaf_air_temp/thermal_analysis.py`**
Computes leaf-to-air temperature differences from the thermal images (median of the coolest 50 % of the pixels per plant) and the air readings in `data/air_readings.parquet`. The JSON lands next to the script (`analysis/leaf_air_temp/thermal_analysis.json`), where the later steps read it. `--data-dir` and `--output` override the defaults.

```
python analysis/leaf_air_temp/thermal_analysis.py --start-date 2025_09_01 --end-date 2025_09_14
```

Sign convention: the JSON stores `diff_sensor1/2` as air minus leaf temperature. The paper and `analysis/combined/run_stats.py` use leaf minus air (positive = leaf warmer than air); `run_stats.py` recomputes it from `leaf_temp_c` and the air readings.

**Step 2 (optional) — `analysis/leaf_air_temp/plot_thermal.py`**
Plots the leaf-to-air temperature difference per bed from the Step 1 JSON (input and output default to `analysis/leaf_air_temp/`).

```
python analysis/leaf_air_temp/plot_thermal.py
```

**Step 3 — `analysis/hyperspectral/calc_spectral_index.py`**
Computes per-bed spectral vegetation indices (NDVI, CI Red Edge, SIPI and the rest of the 21-index set) from the raw hyperspectral cubes, plant-masked with the bundled TorchExport segmentation model, and writes CSV and Excel statistics (and a plot). The defaults match the paper (W1/W2, 3 pm run, Sep 1-14); `--dark-reference` applies the flat-field correction used in the paper and for the tracked workbook. The Excel columns match `data/results_w5_mean_15_sa.xlsx`, so a regenerated workbook can replace it. See [analysis/hyperspectral/README.md](analysis/hyperspectral/README.md) for details and the full index set.

```
cd analysis/hyperspectral
python calc_spectral_index.py --dark-reference --index ndvi,ci_rededge,sipi
```

Output goes to `analysis/hyperspectral/processed/` by default; pass `--csv`/`--xlsx` to write elsewhere (e.g. `--xlsx ../../data/results_w5_mean_15_sa.xlsx`). The masks can also be computed once and reused: `python generate_plant_masks.py` writes `hsi_000_mask.png` into the hyperspectral data folders, and `python calc_spectral_index.py --use-masks` then reads them instead of re-running the model (identical results to <1e-4).

**Step 4 — `analysis/combined/run_stats.py`**
Runs the statistical analyses: the thermal and hyperspectral OLS models (treatment x day) and the Spearman correlations with the `W2` turgor readings. Reads `data/`, the Step 1 JSON and the hyperspectral workbook, and writes intermediate CSVs to `analysis/combined/processed/`. If the background-sensitivity check (Step 7) has been run, it also writes a descriptive thermal-background correlation (`thermal_background_correlation.csv`); otherwise that part is skipped.

```
python analysis/combined/run_stats.py
```

**Step 5 — `analysis/combined/make_figures.py`**
Generates figures from the CSVs of Step 4 into `analysis/figures/`.

```
python analysis/combined/make_figures.py
```

**Step 6 — `analysis/plant_height/run_plant_height_pipeline.py`**
Builds RGB-D point clouds, computes the canopy height (75th percentile of the ExG-segmented vegetation points), plots the W1/W2 trajectories and fits a linear mixed-effects model with a random intercept per plant. Defaults (date windows, thresholds) are in `analysis/plant_height/config.py`; see the [plant height README](analysis/plant_height/README.md).

```
python analysis/plant_height/run_plant_height_pipeline.py
```

**Step 7 (optional) — `analysis/background_sensitivity/`**
Tests whether the pot and soil background around the plants biases each modality's trajectory, by recomputing it at several vegetation thresholds and tracking a background-only signal. Covers thermal, depth (canopy height), RGB and hyperspectral. Method and results are in its [README](analysis/background_sensitivity/README.md).

```bash
cd analysis/background_sensitivity
python compute/thermal_background_sensitivity.py
python compute/depth_background_sensitivity.py    # requires analysis/plant_height PLYs (Step 6)
python compute/rgb_background_sensitivity.py
python compute/hyperspectral_background_sensitivity.py

cd plot
python plot_thermal_background_sensitivity.py
python plot_depth_background_sensitivity.py
python plot_rgb_background_sensitivity.py
python plot_hyperspectral_background_sensitivity.py
python plot_combined_across_thresholds.py   # 2x2 summary; needs all four processed CSVs
```

### 3. Make the paper figures

**`paper/make_results_figure.py`** generates the four-panel results figure (plant height, NDVI, SIPI, leaf-to-air temperature) for W1 vs W2 over September 1-14. It reads the Step 1 JSON, the hyperspectral workbook in `data/`, and `analysis/plant_height/processed/depth_results_w1_w2_values.csv` (Step 6). Days without a reading would be left as a labelled gap; with the ±1 h re-run recovery there are none in this window. Outputs `paper/results_multimodal.pdf` and `.png`.

**`paper/make_example_figure.py`** generates the four-panel modality example (RGB, thermal, NDVI, depth) next to the robot photo `paper/ada.jpg`. It reads images from `data/` (or `--data-dir`) and writes `analysis/figures/example_modalities.png`.

```
python paper/make_results_figure.py
python paper/make_example_figure.py
```

### Expected results

With the steps above you should get the numbers reported in the paper (treatment bed `W2` relative to control `W1`, September 3-14):

| Analysis | Result |
|---|---|
| Plant height (mixed model) | day: +0.00430 m/day (p = 4.61e-14); day x treatment: -0.01308 m/day (p = 3.45e-59) |
| NDVI (OLS, W2:day) | -0.0189/day (p = 1.39e-9) |
| CI Red Edge (OLS, W2:day) | -0.0098/day (p = 6.97e-6) |
| SIPI (OLS, W2:day) | +0.0143/day (p = 1.83e-6) |
| Leaf-air temperature (OLS, W2:day) | +0.596 °C/day (p = 0.030) |
| Spearman with turgor (W2, 12 days) | NDVI ρ = -0.930, CI Red Edge ρ = -0.860, SIPI ρ = 0.944, leaf-air ρ = 0.853 |

## Things to Watch Out For

The full list of known issues is in [docs/data_notes.md](docs/data_notes.md). The main points:

**Acquisition and missing data**
- **Different frame counts per camera, by design.** Each plant and run has 15 RGB, 15 depth, 5 thermal and 1 hyperspectral image (a whole push-broom scan is one capture). Different file counts per modality in a run folder do not mean missing data.
- **Missing runs.** About 6.44 % of the planned camera data (five scheduled runs a day, all 216 plants) could not be collected because of robot or camera problems. A run is usually missing for a whole bed and modality at once; [docs/data_notes.md](docs/data_notes.md) lists every affected date, slot, bed and modality. Two larger gaps: depth is missing from September 19, 15 h, to the end, and the last day (September 22) has only the 06 and 09 h runs.
- **Re-runs at off-schedule hours.** When a scheduled run failed, the robot was often re-run shortly afterwards, so run folders also exist for hours like `07`, `13` or `16`. The hour folder is the local start hour (Europe/Berlin) of the run. Treat a re-run within ±1 h as standing in for the scheduled run (the 6.44 % already does; without re-runs it is 8.5 %). Selecting only the scheduled hours drops these runs. The water-deficit beds `W2` and `W3` have more of these runs than the control bed `W1` (depending on the modality, about 15 to 50 more per plant), often complete flyovers starting at :03 of the following hour (e.g. 07 h), so the `W1` archives are smaller.
- **Timestamps.** `frames_<sensor>.csv` uses Unix epoch nanoseconds, `hsi_000.json` Unix epoch seconds, and the parquet files carry both `berlin_timestamp` and `utc_timestamp`. The data spans the CEST period only.

**Image quality and geometry**
- **A plant folder is a grid position, not an isolated plant.** Each plant folder holds the captures from the robot's stop over that pot's grid position (e.g. `W2_J5`). The images are centred on that plant, but because of the bed layout neighbouring pots and plants are always in view, from the first day on; as the plants grow, their leaves also start to overlap. Isolating the named plant needs segmentation.
- **Dark images.** RGB and hyperspectral images from the early and late runs (06 and 18 h and re-runs near them) are often dark, depending on date and weather.
- **Hyperspectral overexposure.** The hyperspectral exposure is set automatically once per run when the sensor starts, so a change in lighting later in the run can overexpose images.
- **Images are not pixel-registered.** The cameras sit side by side, so the same plant appears at different positions in each modality. Depth, RGB and thermal can be aligned through the calibration ([docs/camera_calibration.md](docs/camera_calibration.md)); the hyperspectral camera is not part of that calibration, and no markers or ground control points were used.
- **Robot positions.** The per-frame robot positions in `frames_<sensor>.csv` are off in places, which shows up as an offset between modalities in the fused point clouds. Some RGB and depth folders from 2025-08-29 and 2025-09-15 have no `frames_*.csv`.
- **Background changes over time.** The plants outgrow their ~10 cm pots by about BBCH 12-14 and then overlap the pot rim, the soil and their neighbours, and the background itself heats and dries under water deficit. Segmentation should not rely on a fixed background. `analysis/background_sensitivity/` shows how the background affects each modality's signal.

**Raw values and units**
- **Hyperspectral cubes are raw** (`uint8` digital numbers, 256 x 256 pixels x 300 bands, read with [hyperio](https://github.com/unklar/hyperio)). Convert them to reflectance with the flat-field correction `C = (R - D) / (W * m - D)`: the gray reference `W` is stored per plant row and run in each `hsi_000.json`, the dark reference `D` is `analysis/hyperspectral/black_reference.npy`, and `m = 2.0`. See [analysis/hyperspectral/README.md](analysis/hyperspectral/README.md).
- **Depth TIFFs have two pages**: page 0 is depth in meters (float32), page 1 the NIR intensity (uint16). Thermal TIFFs hold temperature in °C (float32).
- **Turgor readings are inverted**: a higher `turgic_value` means lower turgor, i.e. more water-deficit stress. The values are raw sensor units, and the clips were attached on September 2.
- **PAR is raw** sensor output in mV, without conversion to µmol m⁻² s⁻¹.
- **Soil readings are not usable.** The soil sensors proved unsuitable for the deployment conditions; the file is included for completeness.

**Metadata and ground truth**
- **RWC samples come from `W3` only.** Each plant was sampled once: one leaf from each of eight plants per sampling day, except on day 12 (September 15), when seven plants gave two leaves each (`<plant>_1` the smallest fully extended leaf, `<plant>_2` the next youngest). The plants and dates are in the RWC sheet. Sampled plants have no BBCH estimate after their sampling date, so keep this in mind when using `W3` imagery from September 4 on. The `rwc` column is empty: compute RWC = (fw - dw) / (sw - dw).
- **Dates in the metadata workbook are day first** (D.M.Y). `metadata_rwc_par_en.xlsx` is the English version of `metadata_rwc_par.xlsx`, with the same sheets.
- **Air sensor positions.** Sensor `1` is at `W3` and sensor `2` at `W1`; there is no air sensor at `W2`.

## Known Gaps

- **Camera calibration script** (`align_test.py`) is not included: it only runs on the calibration recordings (bag files) made for this rig, which are not part of the dataset. The parameters it produced are in `analysis/combined/camera_calibration.py`, and the frames, transforms, reprojection errors and how to apply them are documented in [docs/camera_calibration.md](docs/camera_calibration.md).

## AI Assistance

The code in this repository was developed with assistance from Claude (Anthropic) via Claude Code, and reviewed and checked by the authors. Each source file carries a note to that effect.

## License

The code in this repository is released under the MIT License, see [LICENSE](LICENSE). The ADA 2025 dataset itself is not part of this repository and is published separately on osnaData under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); see the [dataset card](https://doi.org/10.26249/FK2/DMDLTE).
