"""
Grad-CAM visual heatmap generator.

Produces class-activation maps that highlight region-specific synthetic artifacts
(hair, skin boundaries, eyes, frequency noise). The heatmap is blended with the
original media using an OpenCV JET colormap and returned as a Base64 data-URI
for direct client rendering.
"""
from __future__ import annotations

import base64
from typing import Optional, Tuple

import cv2
import numpy as np

try:
    import torch
    import torch.nn as nn
except ImportError as _torch_import_error:  # pragma: no cover - exercised only when torch is absent
    raise ImportError(
        "heatmap_engine.py requires torch/torchvision, which are NOT in the base "
        "requirements.txt (the current live analyzer doesn't use them - see "
        "requirements-ml.txt for why). Install them with "
        "`pip install -r requirements-ml.txt` before importing this module."
    ) from _torch_import_error


class GradCAMHeatmapGenerator:
    """
    Generates Grad-CAM heatmaps from a target convolutional / ViT block.

    Parameters
    ----------
    model : nn.Module
        The detection model (must be in eval mode during inference).
    target_layer : nn.Module
        The layer whose activations and gradients are captured
        (typically the last residual block or final attention block).
    """

    def __init__(self, model: nn.Module, target_layer: nn.Module) -> None:
        self.model = model
        self.target_layer = target_layer
        self.gradients: Optional[torch.Tensor] = None
        self.activations: Optional[torch.Tensor] = None

        # Register hooks once
        self.target_layer.register_forward_hook(self._save_activations)
        self.target_layer.register_full_backward_hook(self._save_gradients)

    def _save_activations(self, module: nn.Module, input: Tuple, output: torch.Tensor) -> None:
        self.activations = output.detach()

    def _save_gradients(self, module: nn.Module, grad_input: Tuple, grad_output: Tuple) -> None:
        self.gradients = grad_output[0].detach()

    def generate_heatmap(
        self,
        input_tensor: torch.Tensor,
        class_idx: Optional[int] = None,
    ) -> np.ndarray:
        """
        Compute a normalized 2-D Grad-CAM map in [0, 1].

        Parameters
        ----------
        input_tensor : torch.Tensor
            Pre-processed batch tensor of shape (1, C, H, W).
        class_idx : int, optional
            Target class index. Defaults to the model's top prediction.

        Returns
        -------
        np.ndarray
            2-D float32 array of shape (H', W') normalized to [0, 1].
        """
        self.model.eval()
        output = self.model(input_tensor)

        if class_idx is None:
            class_idx = int(torch.argmax(output, dim=1).item())

        self.model.zero_grad()
        score = output[0, class_idx]
        score.backward()

        if self.gradients is None or self.activations is None:
            raise RuntimeError("Hooks did not capture gradients or activations.")

        gradients = self.gradients.cpu().numpy()[0]          # (C, H, W)
        activations = self.activations.cpu().numpy()[0]      # (C, H, W)

        weights = np.mean(gradients, axis=(1, 2))            # global average pool
        cam = np.zeros(activations.shape[1:], dtype=np.float32)

        for i, w in enumerate(weights):
            cam += w * activations[i]

        cam = np.maximum(cam, 0)
        max_val = np.max(cam)
        if max_val > 0:
            cam /= max_val
        return cam

    def overlay_heatmap(
        self,
        raw_image_bytes: bytes,
        cam_map: np.ndarray,
        alpha: float = 0.45,
    ) -> str:
        """
        Blend the CAM map over the original image using a JET colormap.

        Returns a Base64-encoded JPEG data-URI ready for <img src="...">.
        """
        nparr = np.frombuffer(raw_image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image bytes for heatmap overlay.")

        h, w = img.shape[:2]
        resized_cam = cv2.resize(cam_map, (w, h), interpolation=cv2.INTER_LINEAR)
        heatmap = cv2.applyColorMap(np.uint8(255 * resized_cam), cv2.COLORMAP_JET)

        overlay = cv2.addWeighted(img, 1.0 - alpha, heatmap, alpha, 0)

        success, buffer = cv2.imencode(
            ".jpg",
            overlay,
            [int(cv2.IMWRITE_JPEG_QUALITY), 90],
        )
        if not success:
            raise RuntimeError("Failed to encode heatmap overlay to JPEG.")

        b64_str = base64.b64encode(buffer).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"


def generate_visual_report(
    model: nn.Module,
    target_layer: nn.Module,
    image_bytes: bytes,
    input_tensor: torch.Tensor,
) -> dict:
    """
    Convenience helper used by the analysis pipeline.

    Returns
    -------
    dict
        {
            "heatmap_base64": str,
            "max_artifact_intensity": float
        }
    """
    generator = GradCAMHeatmapGenerator(model, target_layer)
    cam = generator.generate_heatmap(input_tensor)
    b64_heatmap = generator.overlay_heatmap(image_bytes, cam)
    return {
        "heatmap_base64": b64_heatmap,
        "max_artifact_intensity": float(np.max(cam)),
    }
