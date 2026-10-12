"""Bake vanilla JSON block elements into local-space, material-grouped triangles.

Minecraft coordinates are retained: east +x, up +y, south +z. Face vertex
order and default UV rectangles follow Java's FaceInfo/BlockElement mapping.
This is a resource-model preview, not Minecraft's world/block-entity renderer.
"""
import math

NORMALS = {"east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0), "south": (0, 0, 1), "north": (0, 0, -1)}
BASIS = {
    "east": ((0, 0, -1), (0, -1, 0)), "west": ((0, 0, 1), (0, -1, 0)),
    "up": ((1, 0, 0), (0, 0, 1)), "down": ((1, 0, 0), (0, 0, -1)),
    "south": ((1, 0, 0), (0, -1, 0)), "north": ((-1, 0, 0), (0, -1, 0)),
}


def rotate(point, axis, angle):
    x, y, z = point
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    if axis == "x":
        return (x, c*y - s*z, s*y + c*z)
    if axis == "y":
        return (c*x + s*z, y, -s*x + c*z)
    if axis == "z":
        return (c*x - s*y, s*x + c*y, z)
    raise ValueError("Unknown model rotation axis")


def variant_rotate(point, variant):
    return rotate(rotate(point, "x", -variant.get("x", 0)), "y", -variant.get("y", 0))


def corners(face, low, high):
    x0, y0, z0 = low
    x1, y1, z1 = high
    return {
        "down": [(x0,y0,z1), (x0,y0,z0), (x1,y0,z0), (x1,y0,z1)],
        "up": [(x0,y1,z0), (x0,y1,z1), (x1,y1,z1), (x1,y1,z0)],
        "north": [(x1,y1,z0), (x1,y0,z0), (x0,y0,z0), (x0,y1,z0)],
        "south": [(x0,y1,z1), (x0,y0,z1), (x1,y0,z1), (x1,y1,z1)],
        "west": [(x0,y1,z0), (x0,y0,z0), (x0,y0,z1), (x0,y1,z1)],
        "east": [(x1,y1,z1), (x1,y0,z1), (x1,y0,z0), (x1,y1,z0)],
    }[face]


def default_uv(face, low, high):
    x0, y0, z0 = low
    x1, y1, z1 = high
    return {
        "down": [x0, 16-z1, x1, 16-z0], "up": [x0,z0,x1,z1],
        "north": [16-x1,16-y1,16-x0,16-y0], "south": [x0,16-y1,x1,16-y0],
        "west": [z0,16-y1,z1,16-y0], "east": [16-z1,16-y1,16-z0,16-y0],
    }[face]


def face_uv(face, data, low, high, variant):
    u0, v0, u1, v1 = data.get("uv", default_uv(face, low, high))
    uv = [(u0,v0), (u0,v1), (u1,v1), (u1,v0)]
    shift = int(data.get("rotation", 0)) // 90 % 4
    uv = [uv[(i + shift) % 4] for i in range(4)]
    if variant.get("uvlock"):
        normal = variant_rotate(NORMALS[face], variant)
        destination = max(NORMALS, key=lambda f: sum(a*b for a,b in zip(NORMALS[f], normal)))
        source_u, source_v = (variant_rotate(b, variant) for b in BASIS[face])
        dest_u, dest_v = BASIS[destination]
        # Rotate the UV frame back into the destination's world-facing basis,
        # around the full texture center, including cropped UV rectangles.
        def dot(a,b):
            return sum(x*y for x,y in zip(a,b))
        uv = [(8 + dot(source_u,dest_u)*(u-8) + dot(source_v,dest_u)*(v-8),
               8 + dot(source_u,dest_v)*(u-8) + dot(source_v,dest_v)*(v-8)) for u,v in uv]
    return [(u/16, 1-v/16) for u,v in uv]


def variants(assets, state):
    definition = assets.document("blockstates", state["name"])
    properties = state.get("properties", {})
    chosen, warnings = [], []
    for key, value in definition.get("variants", {}).items():
        condition = dict(p.split("=", 1) for p in key.split(",") if p)
        if assets.matches(condition, properties):
            chosen.append(value)
            break
    for part in definition.get("multipart", []):
        if assets.matches(part.get("when", {}), properties):
            chosen.append(part["apply"])
    result = []
    for choice in chosen:
        if isinstance(choice, list):
            if len(choice) > 1:
                warnings.append("Weighted alternatives use a stable first model, not Minecraft's position-seeded selection")
            choice = choice[0]
        result.append(choice)
    return result, warnings


def texture(assets, textures, value):
    seen = set()
    if isinstance(value, str) and value in textures:
        value = "#"+value  # Some vanilla 26.3 faces use unprefixed variable names.
    while isinstance(value, str) and value.startswith("#"):
        if value in seen:
            return None, False
        seen.add(value)
        value = textures.get(value[1:])
    translucent = isinstance(value, dict) and bool(value.get("force_translucent"))
    if isinstance(value, dict):
        value = value.get("sprite")
    if not isinstance(value, str):
        return None, translucent
    path = assets.path("textures", value, ".png")
    if not path.is_file():
        return None, translucent
    from .assets import resource
    return f"/api/vanilla/{assets.manifest['hash']}/textures/{resource(value)}.png", translucent


def bake(assets, state):
    warnings, groups, models = [], {}, []
    if not assets.manifest:
        return {"parts": [], "models": [], "warnings": ["Local vanilla resources unavailable"]}
    try:
        choices, warnings = variants(assets, state)
        for variant in choices:
            models.append(variant["model"])
            model = assets.model(variant["model"])
            if model.get("transformation"):
                warnings.append("Model-level transformation is unsupported; showing diagnostic fallback")
                continue
            elements = model.get("elements", [])
            if not elements:
                warnings.append("No JSON elements: special/invisible renderer or missing model")
            for element in elements:
                low, high = element["from"], element["to"]
                if len(low) != 3 or len(high) != 3 or any(not math.isfinite(v) for v in low+high):
                    raise ValueError("Invalid model element bounds")
                for face, data in element.get("faces", {}).items():
                    if face not in NORMALS:
                        raise ValueError("Unknown model face")
                    url, translucent = texture(assets, model.get("textures", {}), data.get("texture"))
                    if not url:
                        warnings.append("Missing face texture; diagnostic color used")
                    tint = data.get("tintindex", -1) >= 0
                    foliage = any(word in state["name"] for word in ("leaves", "grass", "fern", "vine", "lily_pad", "sugar_cane"))
                    tint_color = "#82ad57" if tint and foliage else "#ffffff"
                    if tint and not foliage:
                        warnings.append("Block-specific tint is not simulated; neutral tint used")
                    key = (url, translucent, tint_color, element.get("shade", True))
                    part = groups.setdefault(key, {"texture": url, "translucent": translucent, "tint": tint, "tint_color": tint_color, "shade": element.get("shade", True), "positions": [], "uv": []})
                    points = corners(face, low, high)
                    rotation = element.get("rotation")
                    if rotation:
                        origin = rotation.get("origin", [8,8,8])
                        angle, axis = rotation.get("angle", 0), rotation["axis"]
                        scale = 1 / math.cos(math.radians(angle)) if rotation.get("rescale") else 1
                        transformed = []
                        for point in points:
                            relative = rotate(tuple(p-o for p,o in zip(point,origin)), axis, angle)
                            transformed.append(tuple(o + v*(1 if i == "xyz".index(axis) else scale) for i,(o,v) in enumerate(zip(origin,relative))))
                        points = transformed
                    points = [tuple((p+8)/16 for p in variant_rotate(tuple(v-8 for v in point), variant)) for point in points]
                    uv = face_uv(face, data, low, high, variant)
                    for index in (0,1,2,0,2,3):
                        part["positions"].extend(round(v, 8) for v in points[index])
                        part["uv"].extend(round(v, 8) for v in uv[index])
        if not groups and state["name"] not in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}:
            warnings.append("No supported vanilla geometry; diagnostic cube used")
        if state.get("properties", {}).get("waterlogged") == "true":
            warnings.append("Waterlogged fluid overlay is not rendered")
        return {"parts": list(groups.values()), "models": models, "warnings": sorted(set(warnings))}
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        return {"parts": [], "models": models, "warnings": ["Unsupported model definition; diagnostic cube used"]}
