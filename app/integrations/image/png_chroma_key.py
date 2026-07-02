from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    width: int
    height: int


def remove_green_background(data: bytes, *, transition_span: int = 25, green_ratio: float = 1.05) -> ProcessedImage:
    rgb = np.array(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    corner_n = min(10, height, width)
    patches = [
        rgb[:corner_n, :corner_n],
        rgb[:corner_n, -corner_n:],
        rgb[-corner_n:, :corner_n],
        rgb[-corner_n:, -corner_n:],
    ]
    samples = np.concatenate([patch.reshape(-1, 3) for patch in patches])
    mean = samples.mean(axis=0)
    dists = np.sqrt(((samples - mean) ** 2).sum(axis=1))
    threshold_low = int(dists.max()) + 5
    threshold_high = threshold_low + transition_span

    rgba = np.array(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.float32)
    diff = np.sqrt(
        (rgba[..., 0] - mean[0]) ** 2
        + (rgba[..., 1] - mean[1]) ** 2
        + (rgba[..., 2] - mean[2]) ** 2
    )
    span = max(threshold_high - threshold_low, 1)
    alpha = np.clip((diff - threshold_low) / span * 255, 0, 255).astype(np.uint8)
    rgba[..., 3] = alpha

    edge_mask = (rgba[..., 3] > 0) & (rgba[..., 3] < 255)
    opaque_green_mask = (
        (rgba[..., 3] == 255)
        & (rgba[..., 1] > rgba[..., 0] * green_ratio)
        & (rgba[..., 1] > rgba[..., 2] * green_ratio)
    )
    mask = edge_mask | opaque_green_mask
    rgba[mask, 1] = np.minimum(rgba[mask, 1], (rgba[mask, 0] + rgba[mask, 2]) / 2)

    buf = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buf, format="PNG")
    return ProcessedImage(data=buf.getvalue(), width=width, height=height)
