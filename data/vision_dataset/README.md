# vision_dataset — runtime-only files

This is **not** the labeled training dataset (that's kept private and isn't part of this open-source release). It only contains the small protocol/region definition files that `vision/infer.py` and `vision/hud/bench_items.py` need at inference time:

| Path | Purpose |
|---|---|
| `s18-first-v3/protocol/` | board two-stage preprocessing (preprocess/hud_stage/hud_regions/regions.json/anchor) — used by `infer.py` |
| `s18-first-v3/equipment_classes.json` | item class -> display name mapping |
| `s18-equipment-v5.1/protocol/` | item ROI extraction (hud_equipment_v5, 74x34) — used by `infer.py` |
| `s18-equipment-v5.1/equipment_classes.json` | the 137 item classes |
| `s18-equipment-v4/reference/hud-icons/` | 137 23x23 reference icons — used by `bench_items.py` for bench-item matching |
