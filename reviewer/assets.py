"""Local-only vanilla resource cache. Game assets never enter source control or exports."""
import argparse
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import zipfile

from .comparison import ROOT

RESOURCE = re.compile(r"(?:minecraft:)?([a-z0-9_/-]+)$")
MEMBER = re.compile(r"assets/minecraft/(?:textures/[a-z0-9_/-]+\.png|models/[a-z0-9_/-]+\.json|(?:blockstates|items)/[a-z0-9_/-]+\.json)$")
FACES = ("east", "west", "up", "down", "south", "north")
VECTORS = {"east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0), "south": (0, 0, 1), "north": (0, 0, -1)}


def resource(value):
    match = RESOURCE.fullmatch(value or "")
    if not match or ".." in value or "//" in value or match[1].startswith("/") or match[1].endswith("/"):
        raise ValueError("Unsupported vanilla resource identifier")
    return match[1]


def import_client(jar, directory):
    """Copy only allowlisted resource entries, into a new content-addressed cache."""
    jar, directory = Path(jar), Path(directory)
    with jar.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target = directory / digest
    with zipfile.ZipFile(jar) as archive:
        version = json.loads(archive.read("version.json"))["id"]
        entries = [info for info in archive.infolist() if MEMBER.fullmatch(info.filename)]
        if not entries or sum(info.file_size for info in entries) > 128 * 1024 * 1024:
            raise ValueError("Client resource archive missing or exceeds 128 MiB asset limit")
        if any(info.file_size > 8 * 1024 * 1024 for info in entries):
            raise ValueError("Client resource entry exceeds 8 MiB limit")
        target.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            path = target / entry.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(entry))
    manifest = {"version": version, "hash": digest, "textures": sum(e.filename.endswith(".png") for e in entries), "resource_schema": 2}
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(manifest), encoding="utf-8")
    temporary.replace(directory / "manifest.json")
    return manifest


class VanillaAssets:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.manifest = None
        try:
            manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
            if re.fullmatch(r"[a-f0-9]{64}", manifest["hash"]):
                self.manifest = manifest
        except (OSError, ValueError, KeyError):
            pass

    def status(self):
        return {"available": self.manifest is not None, **(self.manifest or {}), "geometry": "vanilla JSON block models"}

    def path(self, category, name, extension):
        if not self.manifest:
            raise KeyError("Vanilla textures have not been imported")
        return self.directory / self.manifest["hash"] / "assets/minecraft" / category / (resource(name) + extension)

    @lru_cache(maxsize=4096)
    def document(self, category, name):
        try:
            return json.loads(self.path(category, name, ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return {}

    def model(self, name, seen=None):
        seen = set() if seen is None else seen
        if name in seen or len(seen) > 24:
            return {}
        seen.add(name)
        model = self.document("models", name)
        parent = self.model(model["parent"], seen) if model.get("parent") else {}
        return {**parent, **model, "textures": {**parent.get("textures", {}), **model.get("textures", {})}}

    @staticmethod
    def matches(condition, properties):
        if "OR" in condition:
            return any(VanillaAssets.matches(c, properties) for c in condition["OR"])
        if "AND" in condition:
            return all(VanillaAssets.matches(c, properties) for c in condition["AND"])
        return all(properties.get(k) in str(v).split("|") for k, v in condition.items())

    def appearance(self, state):
        return self.cached_appearance(state["name"], json.dumps(state.get("properties", {}), sort_keys=True))

    @lru_cache(maxsize=4096)
    def cached_appearance(self, name, properties):
        from .geometry import bake
        from .special import appearance
        state = {"name": name, "properties": json.loads(properties)}
        result = appearance(self, state, bake)
        return {**self.cube_appearance(state), **result}

    def cube_appearance(self, state):
        fallback = {"faces": [None] * 6, "approximate": True}
        if not self.manifest:
            return fallback
        try:
            block = resource(state["name"])
            definition = self.document("blockstates", block)
            properties = state.get("properties", {})
            variant = None
            for key, value in definition.get("variants", {}).items():
                condition = dict(part.split("=", 1) for part in key.split(",") if part)
                if self.matches(condition, properties):
                    variant = value
                    break
            if variant is None:
                variant = next((p["apply"] for p in definition.get("multipart", []) if self.matches(p.get("when", {}), properties)), None)
            if isinstance(variant, list):
                variant = variant[0]  # Stable representative, never randomize review panels.
            if not variant:
                return fallback
            model = self.model(variant["model"])
            textures = model.get("textures", {})

            def resolve(value):
                visited = set()
                while isinstance(value, str) and value.startswith("#"):
                    if value in visited:
                        return None
                    visited.add(value)
                    value = textures.get(value[1:])
                if isinstance(value, dict):
                    value = value.get("sprite")  # 26.3 supports texture descriptors.
                if not isinstance(value, str):
                    return None
                if not value or not self.path("textures", value, ".png").is_file():
                    return None
                return "/api/vanilla/" + self.manifest["hash"] + "/textures/" + resource(value) + ".png"

            by_face = {}
            tinted = {}
            elements = model.get("elements", [])
            for element in elements:
                for face, data in element.get("faces", {}).items():
                    # Cube preview uses full face UVs; partial element UVs remain approximate.
                    by_face.setdefault(face, resolve(data.get("texture")))
                    tinted.setdefault(face, "tintindex" in data)
            representative = next((resolve(v) for v in textures.values() if resolve(v)), None)
            rotated = {}
            rotated_tints = {}
            for face in FACES:
                x, y, z = VECTORS[face]
                for _ in range(int(variant.get("x", 0)) // 90 % 4):
                    y, z = z, -y
                for _ in range(int(variant.get("y", 0)) // 90 % 4):
                    x, z = -z, x
                destination = next(f for f, vector in VECTORS.items() if vector == (x, y, z))
                rotated[destination] = by_face.get(face) or representative
                rotated_tints[destination] = tinted.get(face, False)
            approximate = len(elements) != 1 or (elements and (elements[0].get("from") != [0, 0, 0] or elements[0].get("to") != [16, 16, 16])) or "multipart" in definition
            translucent = any(isinstance(t, dict) and t.get("force_translucent") for t in textures.values())
            return {"faces": [rotated.get(f) for f in FACES], "approximate": bool(approximate), "translucent": translucent, "tints": [rotated_tints.get(f, False) for f in FACES]}
        except (ValueError, KeyError, TypeError):
            return fallback


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import vanilla textures from your own installed Minecraft client JAR")
    parser.add_argument("client_jar")
    args = parser.parse_args()
    print(json.dumps(import_client(args.client_jar, ROOT / "data/reviewer/vanilla")))
