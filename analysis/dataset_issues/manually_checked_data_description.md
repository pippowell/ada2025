# Manually Checked Data

## File

`manually_checked_data.json` is a JSON label file containing manually checked plant/date/hour records.

## Top-level structure

```json
{
  "version": 1,
  "hours": []
}
```

| Field | Type | Contents |
|---|---|---|
| `version` | integer | JSON schema version (`1`). |
| `hours` | array | Reviewed hour records. |

## Hour object

```json
{
  "plant": "W1_B5",
  "date": "2025_08_12",
  "hour": "06",
  "reviewed_by": "reviewer 1",
  "flags": {}
}
```

| Field | Type | Contents |
|---|---|---|
| `plant` | string | Plant identifier. |
| `date` | string | Dataset date in `YYYY_MM_DD` format. |
| `hour` | string | Two-digit hour. |
| `reviewed_by` | string | Anonymized reviewer ID, `reviewer 1` through `reviewer 4`. |
| `flags` | object | Frame/array-level issue labels. Empty means no issues were recorded. |

## Flag object

`flags` is keyed by frame or sensor-array identifier:

- `rgb_000`, `depth_000`, `intensity_000`, `thermal_000` — numbered frames.
- `hsi_mean`, `hsi_rgb`, `hsi_rgb_raw`, `hsi_ndvi`, `hsi_mcari` — HSI representations.
- `3d_rgb`, `3d_intensity`, etc. — 3D/point-cloud labels.

A flag contains:

```json
{
  "error": true,
  "issues": ["Dark", "Medium Blurred"]
}
```

| Field | Type | Contents |
|---|---|---|
| `error` | boolean | Always `true` for a flagged frame. |
| `issues` | array of strings | Issue labels assigned to that frame. |

All flags use the `issues` array.

## Contents

- **1,860** hour records
- **8** plants
- **43** dates, from `2025_08_11` through `2025_09_22`
- **25,797** flags
- **144** hour records with empty `flags`
- **2,059** `Not Centered` flags

### Hour records per plant

| Plant | Hour records |
|---|---:|
| `W1_B5` | 211 |
| `W1_E4` | 211 |
| `W1_G6` | 211 |
| `W1_K6` | 211 |
| `W2_B3` | 254 |
| `W2_G2` | 254 |
| `W2_I6` | 254 |
| `W2_Q6` | 254 |

### Hour records per reviewer ID

| Reviewer ID | Hour records |
|---|---:|
| `reviewer 1` | 461 |
| `reviewer 2` | 476 |
| `reviewer 3` | 461 |
| `reviewer 4` | 462 |

### Flags per sensor

| Sensor | Flags |
|---|---:|
| `rgb` | 17,680 |
| `intensity` | 2,419 |
| `depth` | 2,363 |
| `hsi` | 1,784 |
| `3d` | 844 |
| `thermal` | 707 |

### Not Centered flags per sensor

| Sensor | `Not Centered` flags |
|---|---:|
| `rgb` | 1,661 |
| `depth` | 190 |
| `thermal` | 162 |
| `3d` | 46 |

### Issue labels

| Issue | Occurrences |
|---|---:|
| `Dark` | 9,467 |
| `Missing` | 5,629 |
| `Medium Dark` | 4,061 |
| `Medium Bright` | 2,757 |
| `Medium Blurred` | 2,414 |
| `Not Centered` | 2,059 |
| `Blurred` | 793 |
| `Artifact` | 439 |
| `Medium Artifact` | 344 |
| `Bright` | 200 |
| `water hose` | 90 |
| `Offset` | 86 |
| `no plant` | 5 |
