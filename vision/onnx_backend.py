"""Vision model onnxruntime backend: session loading + preprocessing helpers. Pure numpy, no torch.

Models live in vision/onnx/*.onnx (single file, weights embedded) + meta.json
(classes/size/mean/std/names). providers default to auto-preferring CUDA
(when onnxruntime-gpu is installed) otherwise CPU -- swap the pip package to
switch backends, no code changes needed.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

VIS = Path(__file__).resolve().parent
ROOT = VIS.parent
ONX = VIS / "onnx"
META = json.loads((ONX / "meta.json").read_text(encoding="utf-8"))


def default_providers():
    avail = ort.get_available_providers()
    pref = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
    return pref or avail


def load_session(name, providers=None):
    providers = providers or default_providers()
    plain = ONX / name
    if not plain.exists():
        raise FileNotFoundError(
            f"Model missing: {plain}\n"
            f"Download the ONNX model pack from the project's GitHub Release "
            f"and extract it into vision/onnx/ (see vision/onnx/README.md)."
        )
    return ort.InferenceSession(str(plain), providers=providers)


def resize_chw(x, size):
    """[C,H,W] float -> [C,h,w] bilinear (cv2 operates on HWC)."""
    h, w = size
    return cv2.resize(x.transpose(1, 2, 0), (w, h), interpolation=cv2.INTER_LINEAR).transpose(2, 0, 1)


def gaussian_anchor(w, h, ax, ay):
    """Identity protocol anchor: gaussian centered at (ax,ay), sigma=10, range 0..1."""
    yy, xx = np.mgrid[:h, :w]
    return np.exp(-((xx - ax) ** 2 + (yy - ay) ** 2) / 200).astype(np.float32)


def softmax(logits):
    e = np.exp(logits - logits.max(-1, keepdims=True))
    return e / e.sum(-1, keepdims=True)
