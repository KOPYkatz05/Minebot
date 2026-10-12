"""Check hidden-target isolation and loss restricted to masked positions."""

from pathlib import Path
import sys
import unittest
from unittest import mock

try:
    import torch
except ModuleNotFoundError as error:
    if error.name != "torch":
        raise
    torch = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
if torch is not None:
    import train_completion as training


@unittest.skipIf(torch is None, "PyTorch training dependency is optional; install it to run training checks")
class TrainingTests(unittest.TestCase):
    def test_masked_input_does_not_depend_on_hidden_answers(self):
        original = torch.zeros((3, 4, 5), dtype=torch.long)
        mask = torch.zeros((1, 3, 4, 5), dtype=torch.bool)
        mask[:, 1, 1:3, 1:4] = True
        changed = original.clone()
        changed[mask[0]] = 1
        first = training.masked_input(original, mask, 2)
        second = training.masked_input(changed, mask, 2)
        self.assertTrue(torch.equal(first, second))
        self.assertTrue(torch.all(first[mask] == 2))

    def test_visible_voxels_receive_no_direct_reconstruction_loss(self):
        logits = torch.zeros((1, 2, 2, 2, 2), requires_grad=True)
        target = torch.zeros((2, 2, 2), dtype=torch.long)
        target[0, 0, 0] = 1
        masks = torch.zeros((1, 2, 2, 2), dtype=torch.bool)
        masks[0, 0, 0, 0] = True
        loss = training.balanced_masked_loss(logits, target, masks, torch.tensor([0]))
        loss.backward()
        gradients = logits.grad.permute(0, 2, 3, 4, 1)
        self.assertTrue(torch.all(gradients[~masks] == 0))
        self.assertTrue(torch.any(gradients[masks] != 0))

    def test_network_rejects_leaked_target_ids(self):
        model = training.CompletionNet(2)
        visible = torch.zeros((1, 3, 3, 3), dtype=torch.long)
        masks = torch.ones_like(visible, dtype=torch.bool)
        with self.assertRaises(ValueError):
            model(visible, masks)

    def test_unknown_download_permission_stops_before_creating_a_run(self):
        with mock.patch.object(training, "load_exercise", return_value=(
                {"training_permission": "unknown"}, None, None, None, None, None)), \
                mock.patch.object(training.data, "new_directory") as create_run:
            with self.assertRaisesRegex(ValueError, "permission is unknown"):
                training.train(Path("unused-exercise"), Path("unused-output"), steps=1)
            create_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
