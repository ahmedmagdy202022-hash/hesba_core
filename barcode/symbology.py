"""BARCODE-001: barcode symbols drawn as SVG, with no third-party library.

EAN-13 for 13-digit numeric codes (what shop scanners and suppliers use) and
Code 128 for everything else (letters, dashes, any length). Both are the
public specifications; the tests pin known reference encodings.
"""

from xml.sax.saxutils import escape


# --- EAN-13 -----------------------------------------------------------------

_EAN_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
_EAN_G = ("0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111")
_EAN_R = ("1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100")
_EAN_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")


def ean13_check_digit(first12):
    digits = [int(d) for d in first12]
    total = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits))
    return str((10 - total % 10) % 10)


def is_ean13(code):
    code = code or ""
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def ean13_modules(code):
    if not is_ean13(code):
        raise ValueError(f"Not a valid EAN-13: {code!r}")
    parity = _EAN_PARITY[int(code[0])]
    left = "".join((_EAN_L if p == "L" else _EAN_G)[int(d)] for p, d in zip(parity, code[1:7]))
    right = "".join(_EAN_R[int(d)] for d in code[7:])
    return "101" + left + "01010" + right + "101"


# --- Code 128 ----------------------------------------------------------------

_C128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
_START_B, _START_C, _CODE_B, _CODE_C, _STOP = 104, 105, 100, 99, 106


def _code128_values(text):
    """Symbol values, switching to set C for runs of 4+ digits (shorter bars)."""

    if any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        raise ValueError("Code 128 labels here take printable ASCII only.")
    values = []
    i, current = 0, None
    while i < len(text):
        run = 0
        while i + run < len(text) and text[i + run].isdigit():
            run += 1
        if run >= 4:
            pairs = run - (run % 2)
            if current != "C":
                values.append(_START_C if current is None else _CODE_C)
                current = "C"
            for j in range(i, i + pairs, 2):
                values.append(int(text[j:j + 2]))
            i += pairs
            continue
        if current != "B":
            values.append(_START_B if current is None else _CODE_B)
            current = "B"
        values.append(ord(text[i]) - 32)
        i += 1
    checksum = (values[0] + sum(v * n for n, v in enumerate(values[1:], start=1))) % 103
    return values + [checksum, _STOP]


def code128_modules(text):
    modules = []
    for value in _code128_values(text):
        bar = True
        for width in _C128[value]:
            modules.append(("1" if bar else "0") * int(width))
            bar = not bar
    return "".join(modules)


# --- SVG ---------------------------------------------------------------------


def barcode_modules(code):
    code = (code or "").strip()
    if is_ean13(code):
        return "ean13", ean13_modules(code)
    return "code128", code128_modules(code)


def barcode_svg(code, module_width=2, height=60, show_text=True):
    """An inline SVG of ``code``; quiet zones included so scanners read it."""

    kind, modules = barcode_modules(code)
    quiet = 11 if kind == "ean13" else 10
    width = (len(modules) + 2 * quiet) * module_width
    text_height = 16 if show_text else 0
    rects = []
    x = quiet * module_width
    run_start, run_len = None, 0
    for index, bit in enumerate(modules + "0"):
        if bit == "1":
            if run_start is None:
                run_start, run_len = index, 0
            run_len += 1
        elif run_start is not None:
            rects.append(f'<rect x="{x + run_start * module_width}" y="0" width="{run_len * module_width}" height="{height}"/>')
            run_start = None
    label = f'<text x="{width / 2}" y="{height + 13}" text-anchor="middle" font-family="monospace" font-size="13">{escape(code)}</text>' if show_text else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="hs-barcode" viewBox="0 0 {width} {height + text_height}" '
        f'width="{width}" height="{height + text_height}" role="img" aria-label="{escape(code)}" data-symbology="{kind}">'
        f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(rects)}</g>{label}</svg>'
    )


def internal_ean13(sequence):
    """A shop-internal EAN-13 (GS1 prefix 20 is reserved for in-store use)."""

    first12 = f"20{int(sequence):010d}"
    if len(first12) != 12:
        raise ValueError("Sequence too large for an internal EAN-13.")
    return first12 + ean13_check_digit(first12)
