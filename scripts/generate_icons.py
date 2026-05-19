from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
PNG_PATH = ASSET_DIR / "running_heatmap_icon.png"
ICO_PATH = ASSET_DIR / "running_heatmap_icon.ico"
ICNS_PATH = ASSET_DIR / "running_heatmap_icon.icns"


def rounded_rectangle_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def draw_icon(size: int = 1024) -> Image.Image:
    scale = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = rounded_rectangle_mask(size, int(210 * scale))

    bg = Image.new("RGBA", (size, size), "#111316")
    bg_draw = ImageDraw.Draw(bg)
    for y in range(size):
        blend = y / max(size - 1, 1)
        bg_draw.line((0, y, size, y), fill=(int(17 + 11 * blend), int(19 + 19 * blend), int(22 + 23 * blend), 255))

    heat = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    heat_draw = ImageDraw.Draw(heat)
    blobs = [
        (260, 710, 230, (252, 76, 2, 170)),
        (455, 520, 290, (255, 188, 59, 130)),
        (650, 355, 240, (255, 74, 124, 140)),
        (710, 660, 190, (75, 208, 255, 110)),
    ]
    for cx, cy, radius, color in blobs:
        cx = int(cx * scale)
        cy = int(cy * scale)
        radius = int(radius * scale)
        heat_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
    heat = heat.filter(ImageFilter.GaussianBlur(int(50 * scale)))

    route = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    route_draw = ImageDraw.Draw(route)
    points = [
        (170, 735),
        (265, 640),
        (330, 685),
        (445, 520),
        (390, 410),
        (535, 360),
        (640, 445),
        (760, 290),
        (835, 350),
    ]
    pts = [(int(x * scale), int(y * scale)) for x, y in points]
    route_draw.line(pts, fill=(255, 255, 255, 235), width=max(14, int(28 * scale)), joint="curve")
    route_draw.line(pts, fill=(252, 76, 2, 255), width=max(8, int(15 * scale)), joint="curve")
    for x, y in pts:
        r = int(19 * scale)
        route_draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 230))
        r2 = int(10 * scale)
        route_draw.ellipse((x - r2, y - r2, x + r2, y + r2), fill=(252, 76, 2, 255))

    img.alpha_composite(bg)
    img.alpha_composite(heat)
    img.alpha_composite(route)
    img.putalpha(mask)
    return img


def save_icns(base: Image.Image) -> None:
    base.save(ICNS_PATH, format="ICNS")


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    base = draw_icon()
    base.save(PNG_PATH)
    base.save(ICO_PATH, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    save_icns(base)


if __name__ == "__main__":
    main()
