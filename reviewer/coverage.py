"""Version-bound representative-state coverage, not a claim of all-state fidelity."""
from collections import Counter
from functools import lru_cache
import json


def representative(definition):
    key = next(iter(definition.get("variants",{})), "")
    properties = dict(p.split("=",1) for p in key.split(",") if p)
    values = {}
    def collect(condition):
        for key,value in condition.items():
            if key in {"OR","AND"}:
                for child in value:
                    collect(child)
            else:
                values.setdefault(key,[]).extend(str(value).split("|"))
    for part in definition.get("multipart",[]):
        collect(part.get("when",{}))
    preferred = {"up":"true", "north":"false", "south":"false", "east":"false", "west":"false"}
    for key,options in values.items():
        choice = preferred.get(key,"false")
        if choice not in options:
            choice = "none" if "none" in options else options[0]
        properties.setdefault(key,choice)
    return properties


@lru_cache(maxsize=4)
def audit(assets):
    if not assets.manifest:
        return {"available":False,"entries":[],"counts":{},"total":0}
    root = assets.directory / assets.manifest["hash"] / "assets/minecraft/blockstates"
    entries = []
    for path in sorted(root.glob("*.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))
        state = {"name":"minecraft:"+path.stem,"properties":representative(definition)}
        appearance = assets.appearance(state)
        has_texture = any(p.get("texture") for p in appearance["parts"])
        category = appearance.get("coverage","unsupported")
        if not has_texture and category != "intentional_invisible":
            category = "unsupported"
        entries.append({"name":state["name"],"properties":state["properties"],"category":category,
                        "models":appearance["models"],"warnings":appearance["warnings"],"has_texture":has_texture})
    return {"available":True,"version":assets.manifest["version"],"asset_hash":assets.manifest["hash"],
            "total":len(entries),"counts":dict(Counter(e["category"] for e in entries)),"entries":entries,
            "scope":"One representative state per installed vanilla blockstate file. Static previews/proxies are not full-fidelity coverage of all states or block-entity data."}


if __name__ == "__main__":
    from .assets import VanillaAssets
    from .comparison import ROOT
    result = audit(VanillaAssets(ROOT / "data/reviewer/vanilla"))
    path = ROOT / "data/reviewer/vanilla/coverage.json"
    path.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"version":result.get("version"),"total":result["total"],"counts":result["counts"],"report":str(path)}))
