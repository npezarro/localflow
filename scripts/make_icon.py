"""Draw the LocalFlow icon (rounded gradient square + white waveform) into assets/.
Writes icon.png (1024) and icon.ico (Windows, multi-size). The macOS .icns is made in
CI from icon.png with Apple's iconutil."""
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")
S = 1024


def draw(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    top, bottom = (124, 140, 255), (74, 60, 200)
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        for x in range(size):
            px[x, y] = c
    mask = Image.new("L", (size, size), 0)
    m = int(size * 0.1)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, size - m, size - m), radius=int(size * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    bars, span = 9, size * 0.54
    step = span / bars
    for i in range(bars):
        weight = 1 - abs(i - (bars - 1) / 2) / (bars / 2)
        h = size * (0.10 + 0.36 * weight * (0.75 + 0.25 * math.sin(i * 1.9)))
        x = size / 2 - span / 2 + step * (i + 0.5)
        w = step * 0.52
        d.rounded_rectangle((x - w / 2, size / 2 - h / 2, x + w / 2, size / 2 + h / 2), radius=w / 2,
                            fill=(255, 255, 255, 255))
    return img


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    big.save(os.path.join(OUT, "icon.png"))
    big.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", sorted(os.listdir(OUT)))
