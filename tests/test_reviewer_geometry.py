"""Portable hand-authored fixtures; no copyrighted game resources in tests."""
import json
import math
from pathlib import Path
import tempfile
import unittest

from reviewer.assets import VanillaAssets
from reviewer.geometry import bake, corners, default_uv, face_uv, variant_rotate
from reviewer.comparison import ROOT


class ModelGeometryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "manifest.json").write_text(json.dumps({"hash": "a"*64, "version": "fixture", "textures": 1}))
        self.assets = VanillaAssets(self.root)
        path = self.assets.path("textures", "block/example", ".png")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")

    def tearDown(self):
        self.temporary.cleanup()

    def document(self, category, name, value):
        path = self.assets.path(category, name, ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def model(self, name, bounds, **extra):
        self.document("models", "block/"+name, {"textures": {"all": "block/example"}, "elements": [
            {"from": low, "to": high, "faces": {f: {"texture": "#all"} for f in ("east", "west", "up", "down", "south", "north")}}
            for low,high in bounds], **extra})

    def state(self, name, properties=None):
        return {"name": "minecraft:"+name, "properties": properties or {}}

    def test_slab_bounds_top_bottom_double_and_cropped_uv(self):
        variants = {}
        for name, low, high in (("bottom", [0,0,0], [16,8,16]), ("top", [0,8,0], [16,16,16]), ("double", [0,0,0], [16,16,16])):
            self.model(name, [(low,high)])
            variants["type="+name] = {"model": "block/"+name}
        self.document("blockstates", "slab", {"variants": variants})
        for name, expected in (("bottom", (0,.5)), ("top", (.5,1)), ("double", (0,1))):
            with self.subTest(name=name):
                result = bake(self.assets, self.state("slab", {"type": name}))
                y = result["parts"][0]["positions"][1::3]
                self.assertEqual((min(y),max(y)), expected)
                self.assertFalse(result["warnings"])
        self.assertEqual(default_uv("south", [0,0,0], [16,8,16]), [0,8,16,16])
        self.assertEqual(default_uv("north", [4,8,2], [12,16,14]), [4,0,12,8])

    def test_uv_rectangles_face_rotation_and_uvlock(self):
        bounds = ([0,0,0], [16,8,16])
        self.assertEqual(face_uv("south", {}, *bounds, {}), [(0,.5),(0,0),(1,0),(1,.5)])
        self.assertEqual(face_uv("south", {"uv": [2,4,10,12], "rotation": 90}, *bounds, {}), [(.125,.25),(.625,.25),(.625,.75),(.125,.75)])
        unlocked = face_uv("up", {}, [0,0,0], [16,16,16], {"y": 90})
        locked = face_uv("up", {}, [0,0,0], [16,16,16], {"y": 90, "uvlock": True})
        self.assertEqual(unlocked[0], (0,1))
        self.assertAlmostEqual(locked[0][0], 1)
        self.assertAlmostEqual(locked[0][1], 1)
        # A partial upper-face rectangle also turns around the texture center.
        partial = face_uv("up", {"uv": [8,0,16,8]}, [8,8,0], [16,16,8], {"y":90,"uvlock":True})
        self.assertAlmostEqual(partial[0][0], 1)
        self.assertAlmostEqual(partial[0][1], .5)

    def test_stair_state_variants_and_geometry_are_distinct(self):
        base = ([0,0,0],[16,8,16])
        bounds = {"straight": [base,([8,8,0],[16,16,16])], "inner": [base,([8,8,0],[16,16,16]),([0,8,8],[8,16,16])], "outer": [base,([8,8,8],[16,16,16])]}
        for name, values in bounds.items():
            self.model(name, values)
        self.document("blockstates", "stairs", {"variants": {f"facing={facing},half={half},shape={shape}": {"model":"block/"+shape,"y":y,"x":180 if half == "top" else 0,"uvlock":True}
            for facing,y in (("east",0),("south",90),("west",180),("north",270)) for half in ("top","bottom") for shape in bounds}})
        results = [bake(self.assets, self.state("stairs", {"facing":"east","half":"bottom","shape":shape}))["parts"] for shape in bounds]
        self.assertNotEqual(results[0], results[1])
        self.assertNotEqual(results[0], results[2])
        bottom = bake(self.assets, self.state("stairs", {"facing":"east","half":"bottom","shape":"straight"}))
        top = bake(self.assets, self.state("stairs", {"facing":"east","half":"top","shape":"straight"}))
        self.assertNotEqual(bottom["parts"], top["parts"])
        for facing in ("east","south","west","north"):
            result = bake(self.assets, self.state("stairs", {"facing":facing,"half":"bottom","shape":"straight"}))
            self.assertTrue(result["parts"])
            self.assertFalse(result["warnings"])

    def test_multipart_assembles_all_connections_and_nested_conditions(self):
        self.model("post", [([6,0,6],[10,16,10])])
        self.model("arm", [([7,6,0],[9,9,6])])
        self.document("blockstates", "fence", {"multipart": [
            {"apply":{"model":"block/post"}},
            {"when":{"north":"true"},"apply":{"model":"block/arm"}},
            {"when":{"AND":[{"east":"true"},{"OR":[{"powered":"true"},{"waterlogged":"false"}]}]},"apply":{"model":"block/arm","y":90,"uvlock":True}},
        ]})
        isolated = bake(self.assets, self.state("fence", {"north":"false","east":"false"}))
        connected = bake(self.assets, self.state("fence", {"north":"true","east":"true","waterlogged":"false"}))
        self.assertEqual(len(isolated["models"]), 1)
        self.assertEqual(len(connected["models"]), 3)
        self.assertEqual(len(connected["parts"][0]["positions"]), 3*len(isolated["parts"][0]["positions"]))

    def test_element_rotation_rescale_and_outward_face_winding(self):
        self.model("angled", [([0,0,7],[16,16,9])])
        model = self.assets.document("models", "block/angled")
        model["elements"][0]["rotation"] = {"origin":[8,8,8],"axis":"y","angle":45,"rescale":True}
        self.document("blockstates", "angled", {"variants":{"":{"model":"block/angled"}}})
        result = bake(self.assets, self.state("angled"))
        self.assertTrue(result["parts"])
        self.assertTrue(all(math.isfinite(v) for p in result["parts"] for v in p["positions"]))
        self.assertAlmostEqual(variant_rotate((0,0,-1), {"y":90})[0], 1)
        for face,normal in {"east":(1,0,0),"west":(-1,0,0),"up":(0,1,0),"down":(0,-1,0),"south":(0,0,1),"north":(0,0,-1)}.items():
            p,q,r,_ = corners(face,[0,0,0],[16,16,16])
            a,b = [q[i]-p[i] for i in range(3)], [r[i]-p[i] for i in range(3)]
            cross = (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
            self.assertGreater(sum(x*y for x,y in zip(cross,normal)), 0)

    def test_special_models_and_weighted_variants_report_limitations(self):
        self.document("models", "block/special", {"parent":"builtin/entity"})
        self.document("blockstates", "special", {"variants":{"":{"model":"block/special"}}})
        special = bake(self.assets, self.state("special"))
        self.assertFalse(special["parts"])
        self.assertTrue(special["warnings"])
        self.model("plain", [([0,0,0],[16,16,16])])
        self.document("blockstates", "weighted", {"variants":{"": [{"model":"block/plain"},{"model":"block/plain","y":90}]}})
        weighted = bake(self.assets, self.state("weighted", {"waterlogged":"true"}))
        self.assertEqual(weighted["models"], ["block/plain"])
        self.assertTrue(any("Weighted" in w for w in weighted["warnings"]))
        self.assertTrue(any("Waterlogged" in w for w in weighted["warnings"]))


@unittest.skipUnless((ROOT / "data/reviewer/vanilla/manifest.json").exists(), "Installed vanilla resources are optional")
class InstalledVanillaModelTests(unittest.TestCase):
    """Integration checks against the user's installed, locally cached client resources."""
    @classmethod
    def setUpClass(cls):
        cls.assets = VanillaAssets(ROOT / "data/reviewer/vanilla")

    def appearance(self, name, **properties):
        result = self.assets.appearance({"name":"minecraft:"+name,"properties":properties})
        self.assertTrue(result["parts"], (name,properties,result["warnings"]))
        self.assertFalse(result["warnings"], (name,properties,result["warnings"]))
        return result

    def test_installed_slab_and_all_forty_stair_states(self):
        for slab_type, limits in (("bottom",(0,.5)),("top",(.5,1)),("double",(0,1))):
            result = self.appearance("oak_slab", type=slab_type)
            y = [v for p in result["parts"] for v in p["positions"][1::3]]
            self.assertEqual((min(y),max(y)), limits)
        unique = set()
        for facing in ("north","south","east","west"):
            for half in ("top","bottom"):
                for shape in ("straight","inner_left","inner_right","outer_left","outer_right"):
                    with self.subTest(facing=facing,half=half,shape=shape):
                        result = self.appearance("oak_stairs",facing=facing,half=half,shape=shape)
                        suffix = "_inner" if shape.startswith("inner") else "_outer" if shape.startswith("outer") else ""
                        self.assertEqual(result["models"], ["minecraft:block/oak_stairs"+suffix])
                        unique.add(json.dumps(result["parts"],sort_keys=True))
        # Left/right corner states can describe the same rotated physical corner.
        self.assertEqual(len(unique),24)

    def test_installed_doors_trapdoors_fences_panes_and_walls(self):
        for half in ("lower","upper"):
            for hinge in ("left","right"):
                closed = self.appearance("oak_door",facing="north",half=half,hinge=hinge,open="false")
                opened = self.appearance("oak_door",facing="north",half=half,hinge=hinge,open="true")
                self.assertNotEqual(closed["parts"],opened["parts"])
        for half,limits in (("bottom",(0,3/16)),("top",(13/16,1))):
            result = self.appearance("oak_trapdoor",facing="north",half=half,open="false")
            y = [v for p in result["parts"] for v in p["positions"][1::3]]
            self.assertEqual((min(y),max(y)),limits)
        result = self.appearance("oak_trapdoor",facing="north",half="bottom",open="true")
        y = [v for p in result["parts"] for v in p["positions"][1::3]]
        self.assertEqual((min(y),max(y)),(0,1))
        for name, absent,present in (("oak_fence","false","true"),("glass_pane","false","true"),("stone_brick_wall","none","low")):
            isolated = self.appearance(name,north=absent,south=absent,east=absent,west=absent,up="true")
            connected = self.appearance(name,north=present,south=present,east=present,west=present,up="true")
            if name == "oak_fence":
                self.assertGreater(len(connected["models"]),len(isolated["models"]))
            # Panes use four no-side pieces when isolated and four arms when connected.
            self.assertNotEqual(connected["parts"],isolated["parts"])
