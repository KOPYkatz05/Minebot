"""Small 3D convolutional masked reconstruction experiment on one building."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import completion_data as data
import litematic_pipeline as pipeline


class CompletionNet(nn.Module):
    def __init__(self, classes, embedding=12, width=24):
        super().__init__()
        self.embed = nn.Embedding(classes + 1, embedding)
        self.layers = nn.Sequential(
            nn.Conv3d(embedding + 4, width, 3, padding=1), nn.ReLU(),
            nn.Conv3d(width, width, 3, padding=1), nn.ReLU(),
            nn.Conv3d(width, width, 3, padding=1), nn.ReLU(),
            nn.Conv3d(width, classes, 1))
        self.mask_id = classes

    def forward(self, visible, mask):
        if torch.any(visible[mask] != self.mask_id):
            raise ValueError("Hidden target IDs leaked into model input")
        embedded = self.embed(visible).permute(0, 4, 1, 2, 3)
        shape = visible.shape[1:]
        axes = [torch.linspace(-1, 1, size, device=visible.device) for size in shape]
        coordinates = torch.stack(torch.meshgrid(*axes, indexing="ij"))
        coordinates = coordinates.unsqueeze(0).expand(visible.shape[0], -1, -1, -1, -1)
        return self.layers(torch.cat((embedded, mask[:, None].float(), coordinates), dim=1))


def masked_input(target, masks, mask_id):
    return torch.where(masks, mask_id, target.expand(masks.shape[0], -1, -1, -1))


def balanced_masked_loss(logits, target, masks, air_class_ids):
    losses = F.cross_entropy(logits, target.expand(masks.shape[0], -1, -1, -1), reduction="none")
    solid = ~torch.isin(target, air_class_ids)
    groups = [masks & solid, masks & ~solid]
    means = [losses[group].mean() for group in groups if torch.any(group)]
    if not means:
        raise ValueError("Training mask contains no positions")
    return torch.stack(means).mean()


def evaluate(model, target, masks, vocab):
    model.eval()
    scores, predictions = [], []
    with torch.no_grad():
        for mask in masks:
            mask_tensor = torch.as_tensor(mask[None], device=target.device)
            visible = masked_input(target, mask_tensor, vocab["mask_input_id"])
            output = model(visible, mask_tensor).argmax(dim=1)[0].cpu().numpy()
            answer = target.cpu().numpy()
            predicted = np.where(mask, output, answer)
            scores.append(data.metrics(answer, predicted, mask, vocab))
            predictions.append(predicted)
    return scores, predictions


def mean_scores(scores):
    keys = ("block_state_accuracy", "non_air_state_accuracy", "air_state_accuracy",
            "occupancy_precision", "occupancy_recall")
    return {key: (float(np.mean([score[key] for score in scores if score[key] is not None]))
                  if any(score[key] is not None for score in scores) else None) for key in keys}


def load_exercise(path):
    meta = json.loads((path / "exercise.json").read_text(encoding="utf-8"))
    vocab = json.loads((path / "vocabulary.json").read_text(encoding="utf-8"))
    archive = json.loads((path / "source.internal.json").read_text(encoding="utf-8"))
    if meta["schema_version"] != 1 or meta["purpose"] != "single_build_completion_debug":
        raise ValueError("Unsupported exercise")
    with np.load(path / "examples.npz", allow_pickle=False) as loaded:
        target, train_masks, probe_masks = (loaded[key].copy() for key in ("target", "train_masks", "probe_masks"))
    if data.array_hash(target) != meta["target_array_sha256"]:
        raise ValueError("Target array hash does not match exercise manifest")
    if meta["source_sha256"] != archive["source_sha256"]:
        raise ValueError("Source archive hash does not match exercise manifest")
    if not np.array_equal(target, data.array_for(archive["regions"][0], vocab)):
        raise ValueError("Target arrays disagree with the source archive")
    if vocab["mask_input_id"] != len(vocab["states"]):
        raise ValueError("Mask token must be outside output classes")
    if any(m.dtype != np.bool_ or tuple(m.shape[1:]) != target.shape
           for m in (train_masks, probe_masks)):
        raise ValueError("Masks must be boolean volumes matching the target")
    if not len(train_masks) or not len(probe_masks) or not np.all(train_masks.reshape(len(train_masks), -1).any(axis=1)):
        raise ValueError("Exercise requires populated training and probe masks")
    if not np.all(probe_masks.reshape(len(probe_masks), -1).any(axis=1)):
        raise ValueError("Probe mask is empty")
    train_hashes = {hashlib.sha256(mask.tobytes()).hexdigest() for mask in train_masks}
    if any(hashlib.sha256(mask.tobytes()).hexdigest() in train_hashes for mask in probe_masks):
        raise ValueError("An identical probe mask occurs in training")
    return meta, vocab, archive, target, train_masks, probe_masks


def train(exercise, destination, steps=200, device_name="auto"):
    if not 1 <= steps <= 5000:
        raise ValueError("Steps must be between 1 and 5000")
    exercise = exercise.resolve()
    meta, vocab, archive, array, train_masks, probe_masks = load_exercise(exercise)
    if meta["training_permission"] not in ("project_generated", "allowed") or not meta.get("permission_evidence"):
        raise ValueError("Training permission is unknown. Record allowed usage and evidence in exercise.json first.")
    torch.manual_seed(meta["seed"])
    rng = np.random.default_rng(meta["seed"])
    device = torch.device(("cuda" if torch.cuda.is_available() else "cpu") if device_name == "auto" else device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is unavailable")
    if device.type == "cpu":
        torch.set_num_threads(min(4, torch.get_num_threads()))
    else:
        torch.cuda.manual_seed_all(meta["seed"])
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.cuda.reset_peak_memory_stats()
    model = CompletionNet(len(vocab["states"])).to(device)
    target = torch.as_tensor(array, dtype=torch.long, device=device)
    masks = torch.as_tensor(train_masks, device=device)
    air_class_ids = torch.as_tensor(data.air_ids(vocab), dtype=torch.long, device=device)
    initial_scores, _ = evaluate(model, target, probe_masks, vocab)
    destination = data.new_directory(destination, data.ROOT / "experiments")
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003, weight_decay=0.0001)
    history = []
    start = time.perf_counter()
    print(f"Device: {device}; classes: {len(vocab['states'])}; parameters: {sum(p.numel() for p in model.parameters()):,}", flush=True)
    for step in range(steps):
        model.train()
        selection = rng.integers(0, len(train_masks), size=2)
        batch_masks = masks[selection]
        inputs = masked_input(target, batch_masks, vocab["mask_input_id"])
        optimizer.zero_grad(set_to_none=True)
        logits = model(inputs, batch_masks)
        loss = balanced_masked_loss(logits, target, batch_masks, air_class_ids)
        if not torch.isfinite(loss):
            raise ValueError("Training loss is not finite")
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        history.append({"step": step + 1, "loss": float(loss.detach().cpu())})
        if (step + 1) % 25 == 0 or step == 0 or step + 1 == steps:
            print(f"Step {step + 1}/{steps}; masked loss {history[-1]['loss']:.4f}", flush=True)
    if device.type == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    final_scores, predictions = evaluate(model, target, probe_masks, vocab)
    train_scores, _ = evaluate(model, target, train_masks[:4], vocab)
    checkpoint = {"state_dict": model.cpu().state_dict(), "classes": len(vocab["states"]),
                  "embedding": 12, "width": 24, "vocabulary": vocab,
                  "source_sha256": meta["source_sha256"], "steps": steps}
    checkpoint_path = destination / "model.pt"
    torch.save(checkpoint, checkpoint_path)
    # Confirm saved weights can be loaded and reproduce the exported prediction.
    saved = torch.load(checkpoint_path, map_location=device, weights_only=True)
    loaded = CompletionNet(saved["classes"], saved["embedding"], saved["width"]).to(device)
    loaded.load_state_dict(saved["state_dict"])
    _, restored_predictions = evaluate(loaded, target, probe_masks[:1], vocab)
    if not np.array_equal(predictions[0], restored_predictions[0]):
        raise ValueError("Saved model did not reproduce the first prediction")
    export = data.export_checked(archive, predictions[0], vocab, destination / "reconstructed.litematic",
                                 "Model reconstruction")
    baseline = json.loads((exercise / "baseline-report.json").read_text(encoding="utf-8"))
    code_files = ("train_completion.py", "completion_data.py", "litematic_pipeline.py")
    report = {"evaluation_scope": meta["evaluation_scope"], "exercise": str(exercise),
              "source_sha256": meta["source_sha256"], "seed": meta["seed"], "steps": steps,
              "training_permission": meta["training_permission"],
              "device": str(device), "gpu_name": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
              "peak_allocated_gpu_mib": torch.cuda.max_memory_allocated() / (1024 ** 2) if device.type == "cuda" else None,
              "training_seconds": elapsed, "torch_version": torch.__version__, "python_version": platform.python_version(),
              "code_sha256": {name: hashlib.sha256((data.ROOT / "tools" / name).read_bytes()).hexdigest()
                              for name in code_files},
              "mask_sampling": "24 fixed training masks; 4 distinct same-building probes never used for gradient updates",
              "architecture": "12-state embedding; 24-channel three-layer 3D conv; mask flag and normalized coordinates",
              "loss": "Equal mean contributions from masked air and masked non-air when both exist",
              "initial_probe_mean": mean_scores(initial_scores), "final_probe_mean": mean_scores(final_scores),
              "final_probe_scores": final_scores, "training_mask_mean_first_four": mean_scores(train_scores),
              "baseline_probe_means": {name: mean_scores(scores) for name, scores in baseline["scores"].items()},
              "history": history, "export_check": export, "checkpoint_reload_matches": True,
              "visible_context_unchanged": bool(np.array_equal(predictions[0][~probe_masks[0]], array[~probe_masks[0]])),
              "in_game_check": "pending_user_check"}
    pipeline.save_json(destination / "training-report.json", report)
    print(json.dumps({"final_probe_mean": report["final_probe_mean"],
                      "baseline_probe_means": report["baseline_probe_means"],
                      "training_seconds": elapsed, "peak_allocated_gpu_mib": report["peak_allocated_gpu_mib"]}, indent=2), flush=True)
    print(f"Reconstruction: {destination / 'reconstructed.litematic'}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    try:
        train(args.exercise, args.output_dir, args.steps, args.device)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"ERROR: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
