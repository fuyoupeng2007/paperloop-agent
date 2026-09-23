"""Generate Windows icon sizes from the rendered original vector artwork."""
from pathlib import Path
from PIL import Image

assets = Path(__file__).resolve().parent / "assets"
with Image.open(assets / "paperloop.png") as source:
    icon = source.convert("RGBA")
    sizes = [(size, size) for size in (16, 24, 32, 40, 48, 64, 96, 128, 256)]
    icon.save(assets / "paperloop.ico", format="ICO", sizes=sizes)
with Image.open(assets / "paperloop.ico") as check:
    actual = check.ico.sizes()
    assert actual == set(sizes), f"Missing icon sizes: {set(sizes) - actual}"
    print("ICO sizes:", sorted(actual))
