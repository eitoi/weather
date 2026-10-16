"""Generate the app icons (mountain ridge + wind lines)."""
from PIL import Image, ImageDraw
from pathlib import Path

out = Path(__file__).resolve().parent.parent / "site" / "icons"
out.mkdir(parents=True, exist_ok=True)


def icon(size, pad=0.0):
    s = size
    im = Image.new("RGB", (s, s), "#1c5cab")
    d = ImageDraw.Draw(im)
    k = s / 512
    for i, c in enumerate(["#2a78d6", "#3987e5"]):
        d.rectangle([0, int(s * (0.25 + 0.12 * i)), s, s], fill=c)
    ridge = [(0, 400), (90, 300), (150, 340), (250, 180), (320, 270), (370, 230), (512, 360), (512, 512), (0, 512)]
    d.polygon([(x * k, y * k) for x, y in ridge], fill="#0d366b")
    snow = [(250, 180), (215, 235), (240, 228), (258, 245), (282, 222)]
    d.polygon([(x * k, y * k) for x, y in snow], fill="#f0efec")
    w = max(2, int(14 * k))
    for y, x0, x1 in [(110, 70, 330), (150, 140, 420)]:
        d.line([(x0 * k, y * k), (x1 * k, y * k)], fill="#f0efec", width=w)
        d.ellipse([(x1 * k - w, y * k - w / 2 - 18 * k), (x1 * k + 30 * k, y * k + w / 2)], outline="#f0efec", width=w)
    return im


for name, size in [("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180), ("maskable-512.png", 512)]:
    icon(size).save(out / name)
print("icons written to", out)
