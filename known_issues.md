# Known issues

[中文](known_issues.zh-CN.md)

Recognition-quality issues currently known in the vision pipeline. If you hit something not listed here, please open an issue.

## Unit identity

- **卡兹克 / Kha'Zix ("螳螂"/"Mantis")**: recognition accuracy is noticeably worse than other units. Double-check this one manually if it shows up in a screenshot.
- **Lesser Champion Duplicator ("次级英雄复制器", a consumable)**: using this item creates a duplicate of a unit on your board. There's no dedicated handling for these duplicates, so they're frequently misclassified as Lux transforming into her "月蚀骑士" (Eclipse Knight) form. If you see an unexpected Eclipse Knight Lux on your board, check whether it's actually a duplicate of some other unit created by this consumable.

## Item recognition

- **海妖之怒 / Kraken's Fury**: recognition accuracy for this item is noticeably worse than other items. Double-check it manually.
- **Opponent's board (`mode=enemy`) items are not recognized at all.** There's no calibrated/validated item-row geometry for the opponent's camera angle, so `infer.py` skips item detection entirely for enemy-mode recognition rather than return unreliable guesses — you'll need to fill in opponent items manually if you want them tracked. (An earlier version of this pipeline did attempt enemy item detection and could misattribute detections; this was fixed by disabling it outright for `mode=enemy`, see `vision/infer.py`'s `BoardRecognizer.recognize`.)

## Not yet covered

- Emblems / ordinary radiant items: fine on synthetic training data, not yet verified against enough real screenshots.
- Radiant *stacking-counter* items (e.g. Radiant Guinsoo's Rageblade): known miss on real screenshots — see `vision/README.md`.
- Anything outside the full-health planning-phase, front-facing camera angle (mid-combat, other resolutions, other camera angles): not validated.
