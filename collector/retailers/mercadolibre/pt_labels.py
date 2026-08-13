"""Portuguese-aware spec label mapping for Mercado Libre.

Raw retailer text is never translated or overwritten. These maps only
project Portuguese (and bilingual) labels onto English structured keys.
"""

from __future__ import annotations

import re
from typing import Optional

# Canonical English field <- Portuguese / bilingual labels (lowercase).
SPEC_LABEL_TO_ENGLISH: dict[str, str] = {
    "processador": "processor",
    "processador (cpu)": "processor",
    "processador cpu": "processor",
    "cpu": "processor",
    "chip": "processor",
    "processor": "processor",
    "modelo do processador": "processor",
    "tipo de processador": "processor",
    "memória": "memory",
    "memoria": "memory",
    "memória ram": "ram",
    "memoria ram": "ram",
    "ram": "ram",
    "capacidade de memória": "ram",
    "capacidade de memoria": "ram",
    "armazenamento": "storage",
    "capacidade de armazenamento": "storage",
    "ssd": "storage",
    "hd": "storage",
    "disco rígido": "storage",
    "disco rigido": "storage",
    "storage": "storage",
    "tela": "display",
    "tela tamanho": "display",
    "tamanho da tela": "display",
    "display": "display",
    "ecrã": "display",
    "ecra": "display",
    "resolução da tela": "display",
    "resolucao da tela": "display",
    "sistema operacional": "operating_system",
    "sistema operativo": "operating_system",
    "sistema operacional (os)": "operating_system",
    "so": "operating_system",
    "os": "operating_system",
    "operating system": "operating_system",
    "placa de vídeo": "gpu",
    "placa de video": "gpu",
    "placa de vídeo dedicada": "gpu",
    "placa de video dedicada": "gpu",
    "gráficos": "gpu",
    "graficos": "gpu",
    "graphics": "gpu",
    "gpu": "gpu",
    "marca": "brand",
    "marca do produto": "brand",
    "brand": "brand",
    "modelo": "model",
    "model": "model",
    "mpn": "mpn",
    "part number": "mpn",
    "número de peça": "mpn",
    "numero de peca": "mpn",
    "gtin": "gtin",
    "ean": "gtin",
    "upc": "gtin",
    "código universal": "gtin",
    "codigo universal": "gtin",
}

_WS = re.compile(r"\s+")


def fold_label(label: str) -> str:
    text = (label or "").strip().lower()
    text = text.replace("ó", "o").replace("á", "a").replace("é", "e")
    text = text.replace("í", "i").replace("ú", "u").replace("ã", "a")
    text = text.replace("õ", "o").replace("ç", "c")
    return _WS.sub(" ", text)


def english_spec_key(raw_label: str) -> Optional[str]:
    """Return English canonical key for a retailer spec label, or None."""
    if not raw_label:
        return None
    lowered = raw_label.lower().strip()
    if lowered in SPEC_LABEL_TO_ENGLISH:
        return SPEC_LABEL_TO_ENGLISH[lowered]
    folded = fold_label(raw_label)
    if folded in SPEC_LABEL_TO_ENGLISH:
        return SPEC_LABEL_TO_ENGLISH[folded]
    for alias, key in SPEC_LABEL_TO_ENGLISH.items():
        if alias in lowered or alias in folded:
            return key
    return None


def normalize_spec_map(specs: dict[str, str]) -> dict[str, str]:
    """Project raw (often PT) spec labels to English keys. First match wins."""
    out: dict[str, str] = {}
    for raw_key, value in (specs or {}).items():
        if not value:
            continue
        en = english_spec_key(str(raw_key))
        if not en or en in out:
            continue
        out[en] = str(value).strip()
    return out
