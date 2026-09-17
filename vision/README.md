# Vision recognition: units/stars (classification) + items (detection)

Fixed-template crops + purpose-built models, recognizing your own board from a single **1920x1080 screenshot taken during planning phase**. **Only the forward-facing planning-phase camera angle is supported.** All three models have been validated on real screenshots from the CN server (2026-09).

Runtime region/protocol definitions live in `data/vision_dataset/`:
- units/stars: `data/vision_dataset/s18-first-v3/`
- items: `data/vision_dataset/s18-equipment-v5.1/` (74x34 crops, incl. 11 "stacking counter" item classes; see its protocol files)

The trained weights (`vision/onnx/*.onnx`) are distributed separately as a release asset — see `vision/onnx/README.md`. Training code and the labeled training images are **not** part of this open-source release.

## Three models

| Task | Type | Classes | Input | Model | Synthetic test acc. |
|---|---|---|---|---|---|
| unit identity | classification | **81** (incl. empty, incl. 10 jungle monsters) | 180x205 RGB **+ anchor position heatmap (4ch)** | EfficientNet-V2-S | 0.999 |
| star level | classification | 3 (1/2/3★) | 40x26 health-bar-row crop | ResNet18 | 1.0 |
| items | detection | **137** (incl. emblems/radiant/support) | **74x34 health-bar item row** (incl. stacking counters) | YOLO11s | 0.984 |

> The **anchor channel** is what makes this work: it marks this slot's target point so the classifier recognizes "this slot's" unit rather than whichever neighboring unit happens to dominate the crop.
>
> ⚠️ Synthetic-data scores don't represent real-screenshot accuracy; see the validation notes below.

## Two-stage inference

```
Full 1920x1080 screenshot
  stage 1: preprocess.py (v3) crops 37 slots (context+anchor) ──▶ identity classifier (incl. empty / low-confidence reject)
                                                                └─▶ occupied = list of non-empty slot keys
  stage 2a: hud_stage.py (v3)     locates health bars for occupied slots ──▶ star crop (40x26) ──▶ star classifier
  stage 2b: hud_equipment_v5.py   locates health bars for occupied slots ──▶ item row (74x34) ──▶ item YOLO
  health-bar localization failure/ambiguity/contamination ──▶ that slot's star/items are marked unresolved
  (protocol: never guess 1-star / no-items when unresolved)
```
Item crop is `[bx-5, by+11, 74, 34]` (each unit's own health-bar item row, no crosstalk between neighbors); star level uses a separate crop, independent path.

## Inference (full screenshot -> structured board)

```bash
python vision/infer.py <screenshot_1920x1080.png> [--conf 0.5] [--eqconf 0.35] [--json out.json]
```
Outputs a structured board (unit/star/items + per-field confidence + `unresolved` markers), matching the main project's `BOARD_PROTOCOL`. Convergence-form units (e.g. Portal/Tree of Life transformations) are mapped to their canonical name via `FORM_OVERRIDE`, aligned with the main project's entity names.

## Real-screenshot validation notes (2026-09)

- Unit identity/position/star level: essentially all correct on real CN-server planning-phase screenshots (incl. jungle monsters and transform forms).
- Items: basic components/completed items/ordinary stacking-counter items (e.g. Guinsoo's) detect correctly; empty item rows have zero false positives.
- **Known gap**: radiant stacking-counter items (e.g. Radiant Guinsoo's) miss detection on real screenshots — a sim-to-real appearance gap for the radiant icon (synthetic data scores 0.98, but the icon itself still misses on real images; the digit-size issue was already fixed in v5.1). This is a rare double-edge case (radiant + counter) that's accepted as a known limitation.

## Known limitations

- Health-bar detection thresholds (`hud_regions.py` color/shape heuristics) are tuned for full-health planning phase — that's the best-supported scenario. Real screenshots work well in this scenario.
- Opponent-camera view / mid-combat low health / other resolutions: not validated.
- Emblems / ordinary radiant items: fine on synthetic data, unverified on real samples (insufficient real samples).
- **Item recognition only runs for your own board (`mode=ally`)**. It's deliberately disabled for `mode=enemy` (see `infer.py`'s `BoardRecognizer.recognize`) — there's no calibrated/validated item-row geometry for the opponent's board.

See [`../known_issues.md`](../known_issues.md) for the current list of known recognition mistakes (specific units/items).
