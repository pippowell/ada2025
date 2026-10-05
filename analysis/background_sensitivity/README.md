# Background Sensitivity Check

Sugar beet plants overgrow their ~10cm pots by BBCH 12-14, so soil/pot
background could bias the sensor response reported for the exemplary
analyses. This folder tests that: it recomputes each modality's signal at
several vegetation-masking thresholds, and separately tracks a
background-only (soil/pot) channel. It covers all four imaging modalities
used in the paper: thermal, depth (canopy height), RGB, and hyperspectral.

## Layout

```
compute/   one script per modality — reads raw sensor data, writes a long-format CSV to processed/
plot/      one script per modality — reads processed/*.csv, writes a comparison figure to analysis/figures/
processed/ generated CSVs (gitignored; run compute/ scripts to regenerate)
```

Run a compute script, then its plot counterpart, from within this folder
structure (plot scripts `import common` from their own directory):

```bash
python compute/thermal_background_sensitivity.py
cd plot && python plot_thermal_background_sensitivity.py && cd ..

python compute/depth_background_sensitivity.py
cd plot && python plot_depth_background_sensitivity.py && cd ..

python compute/rgb_background_sensitivity.py
cd plot && python plot_rgb_background_sensitivity.py && cd ..

python compute/hyperspectral_background_sensitivity.py
cd plot && python plot_hyperspectral_background_sensitivity.py && cd ..

# combined figure — needs all four processed/*.csv above to exist first
cd plot && python plot_combined_across_thresholds.py && cd ..
```

All four default to the Sept 1-14 / 3pm / W1 vs W2 window used in the
paper's Fig. 4, and to the same 5 plants per bed as the paper's exemplary
sub-sample (`A2, A8, J5, R1, R7`).

## Method, per modality

Every script asks the same two questions in modality-appropriate form:

1. **Does the published W1-vs-W2 divergence survive stricter/looser
   vegetation-masking thresholds?** If direction and rough magnitude hold
   across a wide sweep, the reported effect isn't an artifact of one
   threshold choice.
2. **Does a background-only channel (soil/pot, no plant material) carry
   its own W1-vs-W2 signal?** If it does, background is not neutral —
   it moves with treatment (e.g. the soil dries alongside the plant), which
   is worth stating regardless of what happens to the plant-side sweep.

- **Thermal** (`thermal_background_sensitivity.py`) — the published
  pipeline (`analysis/leaf_air_temp/thermal_analysis.py`) uses the
  coolest 50% of pixels per TIF as a canopy proxy (leaves run cooler via
  transpiration). Sweeps cool-fraction thresholds 10/25/50/75%; background
  proxy = warmest 25% of pixels (sunlit soil/pot).
- **Depth** (`depth_background_sensitivity.py`) — reuses the
  already-calibrated, RGB-aligned point clouds from
  `analysis/plant_height/step_01_build_pointclouds.py` (no re-registration
  needed; those PLYs already carry a per-point EXG channel from proper
  RGB<->depth calibration). Sweeps the EXG vegetation threshold
  (-5/5[published]/20/40) on canopy height (z_p25); background proxy =
  median depth of points with EXG <= -5 (soil/pot surface).
- **RGB** (`rgb_background_sensitivity.py`) — native full-resolution EXG
  on frame 007 (same definition and threshold as the height pipeline).
  Tracks vegetation *fraction of frame* directly (the most literal
  picture of "plants overgrowing their pots") and background brightness
  (mean intensity of EXG <= -5 pixels).
- **Hyperspectral** (`hyperspectral_background_sensitivity.py`) — HSI is
  *not* geometrically co-registered to RGB/depth, so an
  RGB-derived mask can't be reprojected onto HSI pixels. Instead, EXG is computed directly
  from three visible-range bands **within the same HSI cube** used for
  the paper's spectral indices (~450/550/670nm), so mask and index always
  share one pixel grid with no registration step. Thresholds are
  percentile-based on each image's own EXG distribution (top 10/25/50/75%
  EXG = vegetation), matching the thermal script's approach. Background proxy = bottom 25% EXG (soil/pot), NDVI computed
  the same way as the vegetation-side.

## Results (run 2026-10-01 on the re-extracted data, Sept 1-14, W1/W2, 5 plants/bed)

Each value is the mean over Sept 1-14 of the daily W2 - W1 difference of the per-bed means. Thermal includes the W2 2025-09-11 reading from the 16:00 re-run (see the main README, "Missing data and re-runs").

**Direction is stable across every threshold, in every modality tested.**
The core paper finding — W2 (water-deficit) shows reduced
vigor/transpiration relative to W1 (control) — holds even at the
strictest vegetation-only threshold in all four modalities:

| Modality | Strictest-threshold divergence (W2 vs W1) | Published-threshold divergence |
|---|---|---|
| Thermal (leaf temp) | +3.64°C (cool_10) | +4.10°C (cool_50) |
| Depth (canopy height, z_p25) | +0.041 m farther/shorter (exg_40) | +0.096 m (exg_5) |
| RGB (vegetation fraction) | -0.026 (exg_40) | -0.168 (exg_5) |
| Hyperspectral (NDVI) | -0.098 (top 10% EXG) | -0.177 (top 50% EXG) |

**But every modality's background-only channel also carries a
same-direction, non-trivial signal** — background is not a neutral,
treatment-independent region in this dataset:

| Modality | Background signal (W2 vs W1) | Plausible cause |
|---|---|---|
| Thermal | Soil/pot +5.45°C warmer | Drier soil around W2 = less evaporative cooling |
| Depth | Soil/pot surface +0.037 m farther | Soil pulls in from the pot sides and collapses as it dries, increasing its distance from the camera |
| RGB | Background brightness +14.9 (0-255) | Drier soil visually lighter/brighter |
| Hyperspectral | Background NDVI -0.082, near-monotonic decline | Classic "soil line" effect — bare soil reflectance shifts with moisture |

The hyperspectral background trend is the cleanest of the four: W1's
background NDVI stays flat (~0.06-0.09) for the full two weeks while W2's
declines almost monotonically to below zero — nearly as legible a
treatment signal as the canopy-side NDVI itself. The depth background
signal has the same physical origin as the other three: as the soil dries
under the water-deficit treatment it pulls in from the sides and collapses,
which increases the distance from the depth camera to the soil surface (the
same drying that drives the thermal, brightness, and reflectance shifts).
The paper's Limitations section makes the same point.

## Interpretation

(a) The reported multimodal divergence between W1 and W2 is robust in
direction across a wide range of vegetation-masking thresholds, in every
modality tested. (b) Background (soil/pot) signal is not
treatment-independent in any modality — plausibly because soil moisture
itself differs by design between the beds. The background channel is
therefore best read as a second, supporting readout of the treatment, not
as independent noise to be regressed out: it likely sits downstream of
the treatment, so using it as a covariate would condition on a mediator
(see `analysis/combined/run_stats.py`, which reports only a descriptive
correlation). Absolute effect magnitudes should be read with that caveat.

Full numbers and plots: `processed/*.csv`,
`analysis/figures/{thermal,depth,rgb,hyperspectral}_background_sensitivity.png`,
and the combined across-threshold view,
`analysis/figures/background_sensitivity_across_thresholds.png` (2x2 grid,
one across-threshold trajectory panel per modality — the single clearest
figure for "the threshold choice doesn't change the finding").

## Caveats

- RGB and depth checks use a single frame per plant/date (frame index 7,
  matching the height pipeline), not an average across a burst like the
  thermal check's 5 TIFs — day-to-day RGB/depth values are noisier as a
  result.
- The depth background shift is attributed to soil collapse as it dries
  (see above). This is a physical interpretation, not something the
  depth-only check tests directly.
