"""One cheap Flash look at a camera-roll frame: does it start the next lot?"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from app.config import google_api_key
from app.core.gemini import _generate_content
from app.services.image_preprocess import prepare_gemini_image_file

_HINT_PROMPT = """This is one photo from a bulk camera-roll import, in the order it was shot.

starts_new_lot is true when this frame begins a new estate lot:
- a colored estate tape ROLL or a tape strip with a lot number is in the frame to mark the lot,
  even when the items for that lot are also on the table (the roll sits beside the jewelry or the masks)
- OR the frame is a blank divider (blank paper, empty wall, bare floor, covered lens) with no merchandise

starts_new_lot is false for a normal item photo that has no tape roll and no tape strip.

Return JSON {"starts_new_lot": bool} only.
"""


def frame_starts_new_lot(path: Path) -> bool:
    if not google_api_key():
        return False
    try:
        img, mime = prepare_gemini_image_file(path, max_edge=512, quality=70)
    except Exception:
        return False
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _HINT_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(img).decode("utf-8"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    text = _generate_content(payload, timeout=40)
    if not text:
        return False
    try:
        res = json.loads(re.sub(r"```json|```", "", text).strip())
    except json.JSONDecodeError:
        return False
    return bool(res.get("starts_new_lot"))
