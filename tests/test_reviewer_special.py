import math
from pathlib import Path
import unittest

from reviewer.assets import VanillaAssets
from reviewer.comparison import ROOT
from reviewer.coverage import audit
from reviewer.special import COLORS, box


class AtlasBoxTests(unittest.TestCase):
    def test_head_entity_net_is_cropped_not_stretched(self):
        part = box([4,0,4],[12,8,12],"/texture.png",(64,32),(0,0))
        self.assertEqual(len(part["positions"]),108)
        self.assertEqual(len(part["uv"]),72)
        self.assertTrue(all(0 <= value <= 1 for value in part["uv"]))
        # Front-face head region is 8..16 horizontally and 8..16 vertically.
        front_uv = part["uv"][12:24]
        self.assertEqual(min(front_uv[0::2]),8/64)
        self.assertEqual(max(front_uv[0::2]),16/64)
        self.assertEqual(min(front_uv[1::2]),.5)
        self.assertTrue(part["static_texture"])


@unittest.skipUnless((ROOT/"data/reviewer/vanilla/manifest.json").exists(),"Local vanilla assets optional")
class InstalledSpecialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = VanillaAssets(ROOT/"data/reviewer/vanilla")

    def rendered(self,name,**properties):
        state = {"name":"minecraft:"+name,"properties":properties}
        before = {"name":state["name"],"properties":dict(properties)}
        result = self.assets.appearance(state)
        self.assertEqual(state,before)
        self.assertTrue(result["parts"],(name,result["warnings"]))
        for part in result["parts"]:
            self.assertTrue(part["texture"],name)
            resource = part["texture"].split("/textures/",1)[1].removesuffix(".png")
            self.assertTrue(self.assets.path("textures",resource,".png").is_file(),resource)
            self.assertTrue(all(math.isfinite(v) for v in part["positions"]+part["uv"]))
        return result

    def test_every_requested_material_has_a_texture(self):
        names = 'chain chest creeper_head cyan_wall_banner ender_chest grass green_wall_banner light_blue_banner light_gray_wall_banner lime_wall_banner orange_banner pink_wall_banner purple_banner red_wall_banner water white_wall_banner wither_skeleton_skull yellow_wall_banner zombie_head'.split()
        for name in names:
            with self.subTest(name=name):
                self.rendered(name,axis="y",facing="north",rotation="0",type="single",level="0")

    def test_all_banner_colors_and_wall_attachments(self):
        for color,hex_color in COLORS.items():
            for suffix in ("banner","wall_banner"):
                with self.subTest(color=color,suffix=suffix):
                    result = self.rendered(color+"_"+suffix,facing="east",rotation="4")
                    self.assertEqual(result["parts"][0]["tint_color"],hex_color)
                    self.assertTrue(any("pattern NBT" in w for w in result["warnings"]))

    def test_all_heads_and_wall_variants(self):
        for name in ("skeleton_skull","wither_skeleton_skull","zombie_head","creeper_head","player_head","piglin_head","dragon_head"):
            self.rendered(name,rotation="3")
            a,b = name.rsplit("_",1)
            self.rendered(a+"_wall_"+b,facing="west")

    def test_chest_states_and_all_shulker_colors(self):
        for base in ("chest","trapped_chest","copper_chest","waxed_copper_chest","exposed_copper_chest","weathered_copper_chest","oxidized_copper_chest"):
            for kind in ("single","left","right"):
                with self.subTest(base=base,kind=kind):
                    result = self.rendered(base,type=kind,facing="south")
                    suffix = "" if kind == "single" else "_"+kind
                    self.assertTrue(result["parts"][0]["texture"].endswith(suffix+".png"))
        self.rendered("ender_chest",facing="east")
        for color in ("",*COLORS):
            for facing in ("up","down","north","south","east","west"):
                self.rendered((color+"_" if color else "")+"shulker_box",facing=facing)

    def test_fluids_levels_invisible_and_legacy_aliases(self):
        for name in ("water","lava","bubble_column"):
            zero = self.rendered(name,level="0")
            seven = self.rendered(name,level="7")
            self.assertGreater(max(zero["parts"][0]["positions"][1::3]),max(seven["parts"][0]["positions"][1::3]))
        for name in ("air","cave_air","void_air","light","barrier","structure_void"):
            result = self.assets.appearance({"name":"minecraft:"+name,"properties":{}})
            self.assertTrue(result["invisible"])
            self.assertFalse(result["parts"])
        for name in ("grass","chain"):
            result = self.rendered(name,axis="y")
            self.assertEqual(result["coverage"],"legacy_alias")
            self.assertTrue(any("original block ID is unchanged" in w for w in result["warnings"]))

    def test_registry_audit_has_no_unaccounted_representative_blocks(self):
        result = audit(self.assets)
        self.assertGreater(result["total"],1000)
        self.assertEqual(sum(result["counts"].values()),result["total"])
        self.assertEqual(result["counts"].get("unsupported",0),0)
        self.assertTrue(result["counts"].get("textured_proxy",0)>0)
        self.assertIn("representative",result["scope"])
        self.rendered("heavy_core")  # Unprefixed face texture-variable syntax.
