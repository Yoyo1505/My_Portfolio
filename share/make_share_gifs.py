from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "share"

SETS = {
    "01-weekly-spend-dashboard.gif": (DOCS / "dash-app" / "media", ["01-login", "02-after-login", "03-resumen", "04-cuenta", "05-pdv", "06-ceco", "08-cierres", "09-tiempo", "12-cashi", "12b-cashi-1", "12c-cashi-2", "13-alertas", "15-dark-theme"], 2600),
    "02-opex-control-dashboard.gif": (DOCS / "opex-control" / "media", ["01-login", "02-summary", "03-hierarchy-division", "05-hierarchy-zone", "07-alerts", "08-alert-series", "09-timeline", "10-pivot", "11-assistant"], 2600),
    "03-payly-support-agent.gif": (DOCS / "support-agent" / "media", ["real-01", "real-02", "real-03", "real-04"], 4200),
    "04-paper-trading.gif": (DOCS / "media", ["paper-1", "paper-2", "paper-3"], 3500),
    "05-portfolio-site-tour.gif": (DOCS / "media", ["site-landing", "site-dash-app", "site-opex", "site-support-agent", "site-paper-trading", "site-quant-lab", "site-chatbot"], 2400),
}
WIDTH = 1100


def build(name, folder, stems, ms):
    frames = []
    for s in stems:
        im = Image.open(folder / f"{s}.png").convert("RGB")
        h = int(WIDTH * im.height / im.width)
        frames.append(im.resize((WIDTH, h), Image.LANCZOS))
    hmax = max(f.height for f in frames)
    frames = [f if f.height == hmax else f.resize((WIDTH, hmax)) for f in frames]
    pal = [f.quantize(colors=128, method=Image.MEDIANCUT) for f in frames]
    path = OUT / name
    if len(pal) == 1:
        frames[0].save(path.with_suffix(".png"))
        pal[0].save(path, save_all=True, duration=ms)
    else:
        pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms, loop=0, optimize=True, disposal=2)
    print(name, f"{path.stat().st_size / 1048576:.1f} MB", len(frames), "frames")


for n, (folder, stems, ms) in SETS.items():
    build(n, folder, stems, ms)
