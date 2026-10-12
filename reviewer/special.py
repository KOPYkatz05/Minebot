"""Static special-renderer previews and labeled compatibility/proxy coverage.

These adapters never rewrite source IDs. Entity atlas UVs use the Java box net;
dynamic world effects and block-entity-specific decorations are not simulated.
"""
import math
from .geometry import corners, rotate, texture, variants

COLORS = {
    "white":"#f9fffe", "orange":"#f9801d", "magenta":"#c74ebd", "light_blue":"#3ab3da",
    "yellow":"#fed83d", "lime":"#80c71f", "pink":"#f38baa", "gray":"#474f52",
    "light_gray":"#9d9d97", "cyan":"#169c9c", "purple":"#8932b8", "blue":"#3c44aa",
    "brown":"#835432", "green":"#5e7c16", "red":"#b02e26", "black":"#1d1d21",
}
ALIASES = {"grass":"short_grass", "chain":"iron_chain", "grass_path":"dirt_path"}
INVISIBLE = {"air","cave_air","void_air","barrier","structure_void","light"}
FACING = {"north":0,"east":90,"south":180,"west":270}


def box(low, high, sprite, atlas=(16,16), offset=None, color="#ffffff", yaw=0, pitch=0,
        opacity=1, translucent=False, faces=None, pivot=(8,8,8)):
    """Build one material group with explicit per-face UVs or the entity box net."""
    dx,dy,dz = [b-a for a,b in zip(low,high)]
    u,v = offset or (0,0)
    rectangles = {
        "west":[u,v+dz,u+dz,v+dz+dy], "north":[u+dz,v+dz,u+dz+dx,v+dz+dy],
        "east":[u+dz+dx,v+dz,u+2*dz+dx,v+dz+dy], "south":[u+2*dz+dx,v+dz,u+2*(dz+dx),v+dz+dy],
        "up":[u+dz,v,u+dz+dx,v+dz], "down":[u+dz+dx,v+dz,u+dz+2*dx,v],
    } if offset is not None else {f:[0,0,*atlas] for f in FACING.keys() | {"up","down"}}
    part = {"texture":sprite,"tint":False,"tint_color":color,"shade":True,"translucent":translucent,
            "opacity":opacity,"positions":[],"uv":[],"static_texture":True}
    for face,rectangle in (faces or rectangles).items():
        points = corners(face,low,high)
        points = [tuple(o+c for o,c in zip(pivot,rotate(rotate(tuple(p-o for p,o in zip(point,pivot)),"x",pitch),"y",-yaw))) for point in points]
        u0,v0,u1,v1 = rectangle
        uv = [(u0/atlas[0],1-v0/atlas[1]),(u0/atlas[0],1-v1/atlas[1]),(u1/atlas[0],1-v1/atlas[1]),(u1/atlas[0],1-v0/atlas[1])]
        for i in (0,1,2,0,2,3):
            part["positions"].extend(round(p/16,8) for p in points[i])
            part["uv"].extend(round(p,8) for p in uv[i])
    return part


def sprite(assets,name):
    return texture(assets,{},"minecraft:"+name)[0]


def special(assets,state):
    name = state["name"].removeprefix("minecraft:")
    props = state.get("properties",{})
    yaw = FACING.get(props.get("facing","north"),0)
    parts,warnings = [],[]
    family = None
    if name in INVISIBLE:
        return {"parts":[],"warnings":[],"models":["viewer:invisible/"+name],"invisible":True,"coverage":"intentional_invisible"}
    if name.endswith("chest") and name in {"chest","trapped_chest","ender_chest"} | {p+c for p in ("","waxed_") for c in ("copper_chest","exposed_copper_chest","weathered_copper_chest","oxidized_copper_chest")}:
        family = "chest"
        kind = props.get("type","single") if name != "ender_chest" else "single"
        base = name.removeprefix("waxed_")
        material = {"chest":"normal","trapped_chest":"trapped","ender_chest":"ender","copper_chest":"copper","exposed_copper_chest":"copper_exposed","weathered_copper_chest":"copper_weathered","oxidized_copper_chest":"copper_oxidized"}[base]
        url = sprite(assets,"entity/chest/"+material+("_"+kind if kind in {"left","right"} else ""))
        x0,x1 = (1,16) if kind == "left" else (0,15) if kind == "right" else (1,15)
        parts = [box([x0,0,1],[x1,10,15],url,(64,64),(0,19),yaw=yaw),box([x0,10,1],[x1,15,15],url,(64,64),(0,0),yaw=yaw)]
        latch_x = 14 if kind == "left" else 0 if kind == "right" else 7
        parts.append(box([latch_x,7,0],[latch_x+(1 if kind != "single" else 2),11,1],url,(64,64),(0,0),yaw=yaw))
        warnings.append("Static closed chest; lid animation, contents and seasonal texture are not simulated")
        if kind != "single":
            warnings.append("Double chest uses its recorded half; adjacent pairing and entity-atlas seams are approximate")
    elif name.endswith("_banner"):
        family = "banner"
        wall = "_wall_banner" in name
        color = name.removesuffix("_wall_banner" if wall else "_banner")
        yaw = yaw if wall else 180 + int(props.get("rotation","0"))*22.5
        cloth = sprite(assets,"entity/banner/base")
        support = sprite(assets,"entity/banner/banner_base")
        # Static cloth occupies two blocks vertically; wall cloth hangs below its anchor.
        top = 15 if wall else 28
        z = 14 if wall else 8
        parts = [box([1,top-26,z-.4],[15,top,z+.4],cloth,(64,64),color=COLORS.get(color,"#ffffff"),yaw=yaw,
                     faces={"north":[1,1,21,41],"south":[21,1,41,41],"west":[0,1,1,41],"east":[21,1,22,41],"up":[1,0,21,1],"down":[21,0,41,1]})]
        parts.append(box([1,top,z-1],[15,top+1.3,z+1],support,(64,64),(0,42),yaw=yaw))
        if not wall:
            parts.append(box([7.3,0,7.3],[8.7,top,8.7],support,(64,64),(44,0),yaw=yaw))
        warnings.append("Static banner silhouette and atlas UVs are approximate; pattern NBT and cloth animation are not rendered")
    elif name.endswith(("_head","_skull")):
        family = "head"
        kind = name.replace("_wall_","_").removesuffix("_head").removesuffix("_skull")
        names = {"skeleton":("entity/skeleton/skeleton",(64,32)),"wither_skeleton":("entity/skeleton/wither_skeleton",(64,32)),"zombie":("entity/zombie/zombie",(64,64)),"creeper":("entity/creeper/creeper",(64,32)),"player":("entity/player/wide/steve",(64,64)),"piglin":("entity/piglin/piglin",(64,64)),"dragon":("entity/enderdragon/dragon",(256,256))}
        if kind not in names:
            return None
        image,atlas = names[kind]
        url = sprite(assets,image)
        wall = "_wall_" in name
        yaw = yaw if wall else 180+int(props.get("rotation","0"))*22.5
        low,high = ([4,4,8],[12,12,16]) if wall else ([4,0,4],[12,8,12])
        parts = [box(low,high,url,atlas,(0,0),yaw=yaw)]
        if kind in {"zombie","player"}:
            parts.append(box([v-.12 for v in low],[v+.12 for v in high],url,atlas,(32,0),yaw=yaw))
        if kind == "piglin":
            parts.extend(box([x,low[1]+1,low[2]+3],[x+2,low[1]+5,low[2]+5],url,atlas,(51,6),yaw=yaw) for x in (2,12))
            warnings.append("Piglin snout/ear geometry is a static approximation")
        if kind == "dragon":
            parts.append(box([5,low[1],low[2]-4],[11,low[1]+4,low[2]+1],url,atlas,(176,44),yaw=yaw))
            warnings.append("Dragon head silhouette and atlas mapping are approximate; jaw animation is not rendered")
        if kind == "player":
            warnings.append("Default Steve head shown; custom profile skins are not fetched or rendered")
        warnings.append("Static head preview; entity-atlas orientation requires visual reference validation")
    elif name.endswith("shulker_box"):
        family = "shulker"
        color = name.removesuffix("_shulker_box") if name != "shulker_box" else ""
        url = sprite(assets,"entity/shulker/shulker"+("_"+color if color else ""))
        direction = props.get("facing","up")
        pitch = {"up":0,"down":180,"north":-90,"south":90,"east":90,"west":90}.get(direction,0)
        yaw = {"east":270,"west":90}.get(direction,0)
        parts = [box([0,0,0],[16,8,16],url,(64,64),(0,28),yaw=yaw,pitch=pitch),box([0,8,0],[16,16,16],url,(64,64),(0,0),yaw=yaw,pitch=pitch)]
        warnings.append("Static closed shulker box; shell atlas, lid animation and contents are approximate")
    elif name in {"water","lava","bubble_column"}:
        family = "fluid"
        liquid = "water" if name == "bubble_column" else name
        level = max(0,min(15,int(props.get("level","0"))))
        height = 16 if level >= 8 else 16*(8-level)/9
        top = sprite(assets,"block/"+liquid+"_still")
        flow = sprite(assets,"block/"+liquid+"_flow")
        color = "#3f76e4" if liquid == "water" else "#ffffff"
        parts = [box([0,0,0],[16,height,16],top,color=color,translucent=liquid=="water",opacity=.65 if liquid=="water" else 1,faces={"up":[0,0,16,16],"down":[0,0,16,16]}),
                 box([0,0,0],[16,height,16],flow,color=color,translucent=liquid=="water",opacity=.65 if liquid=="water" else 1,faces={f:[0,0,8,8] for f in FACING})]
        for part in parts:
            part["static_texture"] = False
        warnings.append("Fluid level uses a static surface; neighbor corner heights, flow direction, bubbles and biome color are approximate")
    elif name in {"end_portal","end_gateway"}:
        family = "portal"
        url = sprite(assets,"entity/end_portal") or sprite(assets,"block/nether_portal")
        parts = [box([0,0,0],[16,16 if name=="end_gateway" else 12,16],url,color="#51758a")]
        warnings.append("Static portal texture; Minecraft portal shader is not simulated")
    if not family:
        return None
    return {"parts":parts,"models":["viewer:static/"+family+"/"+name],"warnings":warnings,"coverage":"special_static","invisible":False}


def appearance(assets,state,bake):
    if not assets.manifest or not state["name"].startswith("minecraft:"):
        return {**bake(assets,state),"coverage":"unsupported"}
    name = state["name"].removeprefix("minecraft:")
    if name in ALIASES:
        target = ALIASES[name]
        result = appearance(assets,{**state,"name":"minecraft:"+target},bake)
        return {**result,"coverage":"legacy_alias","warnings":result["warnings"]+[f"Render-only legacy alias: {name} → {target}; original block ID is unchanged"]}
    result = special(assets,state)
    if result is not None:
        return result
    result = bake(assets,state)
    if result["parts"]:
        return {**result,"coverage":"vanilla_model","invisible":False}
    # Do not disguise a special renderer as accurate geometry. A textured proxy
    # is useful for broad coverage, but is a separately reported audit category.
    urls = []
    try:
        choices,_ = variants(assets,state)
        for choice in choices:
            model = assets.model(choice["model"])
            textures = model.get("textures",{})
            values = [textures.get("particle"),*textures.values()]
            urls.extend(texture(assets,textures,v)[0] for v in values if v)
        for candidate in ("block/"+name,"item/"+name):
            urls.append(sprite(assets,candidate))
        if name == "moving_piston":
            urls.append(sprite(assets,"block/piston_side"))
    except (ValueError,KeyError,TypeError):
        pass
    url = next((u for u in urls if u),None)
    if url:
        return {"parts":[box([0,0,0],[16,16,16],url)],"models":["viewer:textured-proxy/"+name],
                "warnings":["Textured proxy only: this special/dynamic model is not faithfully represented"],"coverage":"textured_proxy","invisible":False}
    return {**result,"coverage":"unsupported","invisible":False}
