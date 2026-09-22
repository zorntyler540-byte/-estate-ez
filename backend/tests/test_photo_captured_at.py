from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.main import nullable_datetime_ddl
from app.services.image_preprocess import bytes_to_gemini_jpeg
from app.services.photo_captured_at import (
    captured_at_from_epoch_ms,
    captured_at_from_filename,
    captured_at_from_image_bytes,
    captured_at_from_path,
    captured_at_from_sidecar,
    parse_exif_datetime,
    write_captured_at_sidecar,
)


def test_postgres_captured_at_ddl_is_timestamp() -> None:
    assert nullable_datetime_ddl("postgresql") == "TIMESTAMP WITH TIME ZONE"
    assert nullable_datetime_ddl("postgres") == "TIMESTAMP WITH TIME ZONE"
    assert nullable_datetime_ddl("sqlite") == "DATETIME"
    from sqlalchemy import Column, DateTime
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateColumn

    compiled = str(
        CreateColumn(Column("captured_at", DateTime(timezone=True))).compile(
            dialect=postgresql.dialect()
        )
    ).upper()
    assert "DATETIME" not in compiled
    assert "TIMESTAMP" in compiled


def test_parse_exif_datetime() -> None:
    assert parse_exif_datetime("2026:09:22 10:04:01") == datetime(2026, 9, 22, 10, 4, 1, tzinfo=timezone.utc)
    assert parse_exif_datetime("nope") is None


def test_epoch_ms() -> None:
    dt = captured_at_from_epoch_ms(1_747_000_000_000)
    assert dt is not None
    assert dt.year >= 2025


def test_sidecar_roundtrip(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    stamp = datetime(2026, 9, 22, 15, 1, tzinfo=timezone.utc)
    write_captured_at_sidecar(img, stamp)
    assert captured_at_from_sidecar(img) == stamp


def test_phone_filename_epoch(tmp_path: Path) -> None:
    ms = 1_747_000_000_123
    img = tmp_path / f"{ms}.jpg"
    img.write_bytes(b"x")
    got = captured_at_from_filename(img)
    assert got is not None
    assert captured_at_from_path(img) == got
    assert abs(got.timestamp() * 1000 - ms) < 1000


def test_exif_datetime_survives_reencode() -> None:
    im = Image.new("RGB", (40, 20), (20, 80, 20))
    exif = Image.Exif()
    exif[0x0132] = "2026:09:22 11:22:33"
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=90, exif=exif)
    data = buf.getvalue()
    assert captured_at_from_image_bytes(data) == datetime(2026, 9, 22, 11, 22, 33, tzinfo=timezone.utc)
    out = bytes_to_gemini_jpeg(data, max_edge=40, quality=80)
    got = captured_at_from_image_bytes(out)
    assert got == datetime(2026, 9, 22, 11, 22, 33, tzinfo=timezone.utc)
