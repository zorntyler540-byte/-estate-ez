"""Child-process cutout. Imported only by ``python -m`` so the model is not loaded in the API."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.plain_background import PLAIN_BG_COLORS, PLAIN_BG_MODEL, composite_cutout, jpeg_bytes_rgb


def cut_one(src: Path, dest: Path, color: tuple[int, int, int], session: object) -> None:
    from rembg import remove

    with Image.open(src) as im:
        rgb = im.convert("RGB")
    cut = remove(rgb, session=session, post_process_mask=True)
    if not isinstance(cut, Image.Image):
        cut = Image.open(BytesIO(cut))
    out = composite_cutout(cut, color)
    dest.write_bytes(jpeg_bytes_rgb(out))


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: plain_background_cut COLOR IN_DIR OUT_DIR", file=sys.stderr)
        return 2
    color_name, in_dir_s, out_dir_s = argv[1], argv[2], argv[3]
    color = PLAIN_BG_COLORS.get(color_name)
    if color is None:
        print(f"Unknown color {color_name}", file=sys.stderr)
        return 2
    try:
        from rembg import new_session
    except Exception as exc:  # noqa: BLE001
        print(f"Background cutout is not installed ({exc})", file=sys.stderr)
        return 1
    in_dir = Path(in_dir_s)
    out_dir = Path(out_dir_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = new_session(PLAIN_BG_MODEL)
    for src in sorted(in_dir.glob("*.jpg")):
        try:
            cut_one(src, out_dir / src.name, color, session)
        except Exception as exc:  # noqa: BLE001
            (out_dir / f"{src.stem}.err").write_text(str(exc)[:400], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
