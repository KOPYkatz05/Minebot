import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fastapi.testclient import TestClient
from reviewer.assets import VanillaAssets, import_client, resource
from reviewer.app import create_app


class VanillaAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        jar = self.root / "client.jar"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("version.json", json.dumps({"id": "fixture"}))
            archive.writestr("assets/minecraft/models/block/column.json", json.dumps({"textures": {"side": "minecraft:block/log", "end": "minecraft:block/end"}, "elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {face: {"texture": "#end" if face in ("up", "down") else "#side"} for face in ("up", "down", "east", "west", "north", "south")}}]}))
            archive.writestr("assets/minecraft/models/block/log.json", json.dumps({"parent": "minecraft:block/column"}))
            archive.writestr("assets/minecraft/models/block/glass.json", json.dumps({"parent": "minecraft:block/column", "textures": {"side": {"sprite": "minecraft:block/log", "force_translucent": True}, "end": {"sprite": "minecraft:block/end", "force_translucent": True}}}))
            archive.writestr("assets/minecraft/blockstates/glass.json", json.dumps({"variants": {"": {"model": "minecraft:block/glass"}}}))
            archive.writestr("assets/minecraft/blockstates/log.json", json.dumps({"variants": {"axis=y": {"model": "minecraft:block/log"}, "axis=x": {"model": "minecraft:block/log", "x": 90, "y": 90}, "axis=z": {"model": "minecraft:block/log", "x": 90}}}))
            for texture in ("log", "end"):
                archive.writestr(f"assets/minecraft/textures/block/{texture}.png", b"fixture-png")
            archive.writestr("assets/minecraft/textures/block/../../escaped.png", b"untrusted")
            archive.writestr("secret.txt", b"untrusted")
        self.manifest = import_client(jar, self.root / "vanilla")
        self.assets = VanillaAssets(self.root / "vanilla")

    def tearDown(self):
        self.temporary.cleanup()

    def test_local_import_allowlist_and_face_inheritance(self):
        self.assertEqual(self.manifest["textures"], 2)
        self.assertFalse((self.root / "secret.txt").exists())
        result = self.assets.appearance({"name": "minecraft:log", "properties": {"axis": "y"}})
        self.assertTrue(result["faces"][2].endswith("/end.png"))
        self.assertTrue(result["faces"][0].endswith("/log.png"))
        self.assertFalse(result["approximate"])

    def test_directional_state_and_unknown_fallback(self):
        result = self.assets.appearance({"name": "minecraft:log", "properties": {"axis": "x"}})
        self.assertTrue(result["faces"][0].endswith("/end.png"))
        self.assertTrue(result["faces"][1].endswith("/end.png"))
        self.assertTrue(result["faces"][2].endswith("/log.png"))
        result = self.assets.appearance({"name": "mod:unknown", "properties": {}})
        self.assertEqual(result["faces"], [None] * 6)
        self.assertTrue(result["approximate"])

    def test_modern_texture_descriptors(self):
        result = self.assets.appearance({"name": "minecraft:glass", "properties": {}})
        self.assertTrue(result["faces"][0].endswith("/log.png"))
        self.assertTrue(result["translucent"])

    def test_api_status_palette_and_opaque_resource_access(self):
        with TestClient(create_app(self.root, worker=False, testing=True)) as client:
            self.assertTrue(client.get("/api/vanilla").json()["available"])
            palette = client.post("/api/vanilla/palette", json=[{"name": "minecraft:log", "properties": {"axis": "x"}}]).json()
            texture = client.get(palette[0]["faces"][0])
            self.assertEqual(texture.status_code, 200)
            self.assertEqual(texture.headers["content-type"], "image/png")
            self.assertEqual(client.get("/api/vanilla/wrong/textures/block/log.png").status_code, 404)
            self.assertEqual(client.get(f"/api/vanilla/{self.manifest['hash']}/textures/secret.png").status_code, 404)
            self.assertEqual(client.post("/api/vanilla/palette", json=[{"name": 42}]).status_code, 400)
            self.assertEqual(client.post("/api/vanilla/palette", json=[], headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_no_assets_is_supported_and_invalid_ids_are_rejected(self):
        self.assertFalse(VanillaAssets(self.root / "absent").status()["available"])
        for value in ("block/../../secret", "other:stone", "block//stone", "/absolute", "block/stone\\other"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resource(value)
