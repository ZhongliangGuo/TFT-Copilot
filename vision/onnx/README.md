# Model weights (not stored in git)

This folder should contain, next to `meta.json` (already checked in):

```
vision/onnx/
├── meta.json          (checked into git)
├── identity.onnx
├── stars.onnx
├── equipment.onnx
└── bench_feat.onnx
```

The four `.onnx` files are ~200MB combined, too large for a git repo, so they're distributed as a separate download:

1. Go to this project's **GitHub Releases** page.
2. Download the latest `onnx-models-*.zip` asset.
3. Extract it so the four `.onnx` files land directly in this folder (overwriting nothing else here).

Without these files, `vision/vision_api.py` will raise a clear `FileNotFoundError` telling you what's missing and where to get it — the rest of the project (backend, frontend) runs fine without them; you just lose screenshot-based board recognition and have to enter your board by hand.
