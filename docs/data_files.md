# Data files and metadata

The ADA 2025 dataset (doi:10.26249/FK2/DMDLTE) consists of the camera data, the sensor
readings and a metadata workbook. This page describes the layout and the
columns of each file. Known data issues are listed in
[data_notes.md](data_notes.md); camera calibration and 3D fusion are in
[camera_calibration.md](camera_calibration.md).

Beds: `W1` is the control bed, `W2` and `W3` are the water-deficit beds.
Plants are named by bed and grid position, e.g. `W2_J5` (row letter `A`-`R`,
column `1`-`8`, checkerboard pattern, 72 plants per bed).

## Camera data

The camera data is published as one archive per modality and bed
(`rgb_W1.zip`, `thermal_W2.zip`, ...; 12 archives). `utils/download_dataset.py`
puts them in `data/<modality>/` (or under `--data-dir`), and `utils/unzip_data.py`
extracts the selected beds and plants next to them:

```
<modality>/                 rgb, depth, thermal, hsi
  <bed>_<plant>/            e.g. W1_A2
    <YYYY_MM_DD>/           e.g. 2025_09_01
      <HH>/                 run start hour; scheduled 06 09 12 15 18, re-runs at other hours
        rgb:      rgb_000.jpg ... rgb_014.jpg         + frames_rgb.csv
        depth:    depth_000.tif ... depth_014.tif     + frames_depth.csv
        thermal:  thermal_000.tif ... thermal_004.tif + frames_thermal.csv
        hsi:      hsi_000.jp2 + hsi_000.json
```

Inside an archive, paths start at the plant folder (`W1_A2/2025_09_01/15/...`),
so extracting it by hand inside a `<modality>/` folder gives the same layout.

| Modality | File | Content |
|---|---|---|
| RGB (Logitech C920) | `rgb_NNN.jpg` | 1920 x 1080, 8-bit RGB |
| Depth (Pico Flexx) | `depth_NNN.tif` | Two pages, 224 x 171: page 0 depth in meters (float32), page 1 NIR intensity (uint16) |
| Thermal (FLIR A70) | `thermal_NNN.tif` | 640 x 480, temperature in °C (float32) |
| Hyperspectral (Resonon Pika L) | `hsi_000.jp2` | Raw cube, 256 x 256 pixels x 300 bands (uint8), one push-broom scan per plant; read with [hyperio](https://github.com/unklar/hyperio) |

The number of frames differs between modalities by design (camera frame
rates): 15 RGB, 15 depth, 5 thermal and 1 HSI per plant and run.

### `frames_<sensor>.csv` (RGB, depth, thermal)

One row per frame.

| Column | Type | Description |
|---|---|---|
| `frame_id` | string, e.g. `000` | Matches the image file number |
| `timestamp` | integer | Capture time, Unix epoch in nanoseconds |
| `position_robot_x`, `position_robot_y`, `position_robot_z` | float | Robot position at capture, in meters (robot frame, see [camera_calibration.md](camera_calibration.md)) |

### `hsi_000.json`

Metadata and the calibration reference for the cube, as written by hyperio.

| Field | Description |
|---|---|
| `shape` | Cube shape `[height, width, bands]` |
| `raw_dtype` | Data type of the raw cube (`uint8`) |
| `wavelengths` | Band center wavelengths in nm (300 values, about 384-1025 nm) |
| `reference_spectrum` | Gray reference spectrum `W` (one value per band), captured once per plant row in each run; all plants of a row share it |
| `reference_multiplier` | `m` in the flat-field correction (2.0, for the 50 % gray panel) |
| `reference_eps` | Small constant added to avoid division by zero |
| `avg`, `normalize`, `cache_normalized`, `input_scale` | hyperio read settings |
| `rgb_band_indices`, `rgb_wavelengths` | Bands used for an RGB preview (about 649/550/476 nm) |
| `metadata.label`, `metadata.row_label`, `metadata.col_id` | Plant grid position (e.g. `A2`, `A`, `2`) |
| `metadata.x`, `metadata.y`, `metadata.x_px`, `metadata.y_px`, `metadata.center` | Plant center in the stitched row image (pixels) |
| `metadata.timestamp`, `metadata.first_line_timestamp`, `metadata.last_line_timestamp` | Capture time (Unix epoch, seconds) of the plant and of the first/last scan line |
| `metadata.distance_m`, `metadata.distance_per_line` | Position along the row (m) and robot travel per scan line (m) |
| `metadata.segment_id`, `metadata.plant_index` | Row segment and plant index used during stitching |

The flat-field correction is `C = (R - D) / (W * m - D)` with the dark
reference `D` (`analysis/hyperspectral/black_reference.npy`); see
`analysis/hyperspectral/README.md`.

## Sensor readings (parquet)

All three files have one row per sensor and reading, with the timestamp in two
time zones.

| Column | Type | Description |
|---|---|---|
| `berlin_timestamp` | datetime, Europe/Berlin | Reading time, local |
| `utc_timestamp` | datetime, UTC | Reading time, UTC |
| `sensor_number` | string | Sensor ID, see the "Sensor Guide" sheet below |

### `air_readings.parquet`

Two BME280 sensors, every 2 min, 2025-08-12 to 2025-09-22 (59,648 rows).

| Column | Unit | Description |
|---|---|---|
| `air_temperature_value` | °C | Air temperature |
| `humidity_value` | % RH | Relative humidity |
| `air_pressure_value` | hPa | Air pressure |

Sensor `1` is placed at W3, sensor `2` at W1 (on shaded wooden blocks at the
bed ends).

### `leaf_readings.parquet`

21 AgriHouse leaf-thickness (turgor) clips, every 2 min, 2025-09-02 to
2025-09-22 (294,247 rows).

| Column | Unit | Description |
|---|---|---|
| `turgic_value` | raw sensor units | Leaf-thickness reading; **higher values mean lower turgor** (increasing water-deficit stress) |

Sensors `1`-`9` are in W3, `10`-`18` in W2, `19`-`21` in W1 (plant positions in
the "Sensor Guide" sheet).

### `soil_readings.parquet`

Four soil sensors in W2, every 2 min, 2025-08-12 to 2025-09-22 (119,292 rows).
These sensors proved unsuitable for the deployment conditions (see the paper)
and the readings are not used in any analysis.

| Column | Unit | Description |
|---|---|---|
| `temp_value` | °C | Soil temperature |
| `cap_value` | raw sensor units | Capacitive soil-moisture reading |

## Metadata workbook (`metadata_rwc_par_en.xlsx`)

English version of `metadata_rwc_par.xlsx` (German original, same sheets).
Dates in the sheets are written day first (D.M.Y).

| Sheet | Content |
|---|---|
| Raw Protocol | Experiment log: date, entry number and note (seeding, sensor installation and moves, irrigation changes, incidents). Notable: before 2025-08-11 the air sensors were in full sun and daytime temperatures read too high. |
| Sensor Guide | Mapping of leaf, soil and air sensor numbers to plant/bed positions and wiring (cable, pins, ESP32 board). Also lists two DFKI air sensors (5534 at W1, 4965 at W3), whose readings are not part of the parquet files. |
| BBCH & Symptom | Per plant (rows `W1 A2` ... `W3 R7`), BBCH estimate and symptom notes on 2, 6, 8, 10, 12, 15 and 22 September (two columns per date). Plants sampled for RWC have no BBCH estimate after sampling. |
| RWC | Relative water content samples: `day` (days after irrigation cutoff), `date` (D.MM), `plant` (grid position in W3), `fw` fresh weight, `sw` turgid (saturated) weight, `dw` dry weight, in g. RWC = (fw - dw) / (sw - dw); the `rwc` column is left empty in the file. |
| PAR | Raw export from the PAR logger (Driesen sensor): 12 header rows, then `No.`, `Date/Time` (D.M.Y local time) and `Measuring Value` in mV, every 15 min, 2025-08-08 to 2025-09-24. Values are the sensor's raw output; no conversion to µmol m⁻² s⁻¹ was applied. |

The leaf scans and the per-plant photos from the last day are in the
`Leaf Scans.zip` and `Plant Images.zip` archives.

## Analysis output (`data/results_w5_mean_15_sa.xlsx`)

Per-bed daily statistics of the 21 spectral indices from
`analysis/hyperspectral/calc_spectral_index.py` (W1/W2, 3 pm run,
Sep 1-14, five plants per bed, spectral window 5, flat-field correction with
the dark reference). One sheet, one row per index, bed and date:

| Column | Description |
|---|---|
| `index` | Index name, e.g. `ndvi`, `ci_rededge`, `sipi` |
| `bed_id` | `W1` or `W2` |
| `group` | Date (`YYYY_MM_DD`) |
| `mean`, `median`, `std`, `min`, `max`, `q25`, `q75`, `iqr` | Statistics of the per-image plant-masked index means across the bed's plants |
| `n_images` | Number of images (plants) contributing |
