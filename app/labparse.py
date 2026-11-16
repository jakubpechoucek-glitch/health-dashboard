"""Čtení textu z laboratorních výsledkových listů (bez FastAPI, jen čisté funkce).

Podporovaná rozložení:
  A) SmartMEDIX „Výsledkový list“:               kód  název  hodnota jednotka  příznaky  ref_od - ref_do
  B) SmartMEDIX „Laboratorní výsledky pacienta“: kód  název  ref_od / ref_do  hodnota jednotka
  C) EUC Lab „Opis výsledku“:                    datum  vzorek  kód  název  hodnota  jednotka  ref_od-ref_do
  D) Unilabs „Výsledkový list“:                  pracoviště  T_název  hodnota  jednotka  ref_od - ref_do  příznaky

Výsledky z různých laboratoří se sjednocují na společné názvy ukazatelů a jednotky, aby šly porovnat v čase.
Čte se jen datum odběru, řádky s výsledky a název laboratoře. Jméno pacienta, číslo pojištěnce, rodné číslo,
lékař ani další identifikátory se nezpracovávají ani neukládají.
"""
import re
import unicodedata
from collections import Counter
from datetime import date


class LabParseError(Exception):
    pass


# Kódy močových testů laboratoří A, B a C: stejný název (Glukóza, Leukocyty…) existuje i v krvi, proto přípona.
MOC = {
    "03364": "Glukóza (moč)", "03413": "Bílkovina (moč)", "03280": "Bilirubin (moč)",
    "03434": "Urobilinogen (moč)", "03410": "pH (moč)", "03371": "Krev (moč)",
    "03388": "Leukocyty (moč, proužek)", "03377": "Ketolátky (moč)", "03399": "Nitrity (moč)",
    "03137": "Specifická hustota (moč)", "03355": "Erytrocyty (moč, sediment)",
    "03384": "Leukocyty (moč, sediment)", "08462": "Bakterie (moč, sediment)",
    "12334": "Hlen (moč, sediment)",
}
# Unilabs označuje vzorek předponou (S sérum, P plazma, B krev, U moč, C výpočet). Moč se pozná podle U_.
UNILABS_MOC = {
    "pH": "pH (moč)", "Hustota": "Specifická hustota (moč)", "Bílkovina": "Bílkovina (moč)", "Glukóza": "Glukóza (moč)",
    "Ketolátky": "Ketolátky (moč)", "Bilirubin": "Bilirubin (moč)", "Urobilinogen": "Urobilinogen (moč)",
    "Nitrity": "Nitrity (moč)", "Leukocyty": "Leukocyty (moč, proužek)", "Krev": "Krev (moč)",
    "Leukocyty sediment": "Leukocyty (moč, sediment)", "Erytrocyty sediment": "Erytrocyty (moč, sediment)",
    "Epitelie dlaždicovité": "Epitelie dlaždicovité (moč, sediment)", "Hlen": "Hlen (moč, sediment)",
    "Bakterie": "Bakterie (moč, sediment)",
}
# Názvy z Unilabs -> společné názvy (stejné, jaké používají laboratoře A až C). Neznámé názvy zůstanou beze změny.
UNILABS_ALIASES = {
    "Kreatinin": "Kreatinin", "CKD-EPI 2021": "GF CKD-EPI", "Kyselina močová": "Kyselina močová", "Urea": "Urea",
    "Na": "Na - natrium", "K": "K - kalium", "Cl": "Cl - chloridy", "Mg": "Mg - hořčík", "ALT": "ALT", "AST": "AST",
    "GGT": "GGT", "ALP": "ALP", "Bilirubin celkový": "Bilirubin celk.", "Bílkovina celková": "Celk.bílkovina",
    "Lipáza": "Lipáza", "Cholesterol": "Cholesterol", "HDL cholesterol": "HDL cholesterol", "LDL cholesterol": "LDL cholesterol",
    "Triacylglyceroly": "Triacylglyceroly", "Non-HDL cholesterol": "non-HDL cholesterol", "Glukóza": "Glukóza",
    "25OH vitamin D": "25-OH vitamin D", "TSH": "TSH", "fT4": "fT4",
    "Leukocyty": "WBC - leukocyty", "Erytrocyty": "RBC - erytrocyty", "Hemoglobin": "HB - hemoglobin",
    "Hematokrit": "HCT - hematokrit", "MCV": "MCV-stř.obj.ery", "MCH": "MCH - bar.k.ery", "MCHC": "MCHC - st.bar.k.",
    "RDW": "RDW", "Trombocyty": "PLT - trombocyty",
}
STRIP_URINE = {"Bílkovina (moč)", "Glukóza (moč)", "Ketolátky (moč)", "Bilirubin (moč)", "Urobilinogen (moč)",
               "Nitrity (moč)", "Leukocyty (moč, proužek)", "Krev (moč)"}

_NUM = re.compile(r"([<>]?)(\d+(?:,\d+)?)")
_REF = re.compile(r"(\d+(?:,\d+)?)\s*-\s*(\d+(?:,\d+)?)")
_REF_A = re.compile(r"(?P<lo>\d+(?:,\d+)?)\s*-\s*(?P<hi>\d+(?:,\d+)?)\s*$")                   # „… 2,8 - 8,1“
_REF_B = re.compile(r"^(?P<lo>\d+(?:,\d+)?)?\s*/\s*(?P<hi>\d+(?:,\d+)?)?\s*(?P<rest>.*)$")      # „2,80 / 8,10  4,2 mmol/L“
_A_ROW = re.compile(r"^\s*(?P<code>\d{5})\s+(?P<name>\S.*?)\s{2,}(?P<rest>\S.*)$")
_C_ROW = re.compile(r"^\s*(?P<d>\d{2}\.\d{2}\.\d{4})\s+(?P<sample>\S+)\s+(?P<code>\d{5})\s+(?P<name>\S.*?)\s{2,}(?P<rest>\S.*)$")
_D_ROW = re.compile(r"^\s*[A-Z]{2,5}(?:\s+AM)?\s+(?P<tissue>[A-Za-z]{1,2})_(?P<rest>\S.*)$")
_DATE_LABEL = re.compile(r"(?:Datum )?[Oo]dběru?:?\s*(\d{2})\.(\d{2})\.(\d{4})")


def _f(s: str | None) -> float | None:
    return float(s.replace(",", ".")) if s else None


def _iso(d: str, m: str, y: str) -> str:
    return date(int(y), int(m), int(d)).isoformat()


def norm_unit(u: str | None) -> str:
    """Sjednotí zápis jednotek mezi laboratořemi (µ/u, ˆ/^, mezery, velikost písmen)."""
    u = (u or "").strip().strip("|").replace("µ", "u").replace("μ", "u").replace("ˆ", "^")
    u = re.sub(r"\s+", "", u)
    return {"1": "", "fl": "fL"}.get(u, u)


def _result(analyte, value, text, unit, lo, hi, note, taken_on) -> dict:
    unit = norm_unit(unit)
    if analyte == "Specifická hustota (moč)" and unit in ("kg/m3", "kg/m^3") and value is not None and value > 100:
        value, unit = value / 1000, "g/cm^3"                           # Unilabs uvádí 1009 kg/m3, ostatní 1,009 g/cm3
        lo, hi = (lo / 1000 if lo is not None else None), (hi / 1000 if hi is not None else None)
    if analyte in STRIP_URINE and unit == "arb.j." and value == 0 and (lo, hi) in ((0, 0), (None, None)):
        value, text, unit, lo, hi = None, "neg", "", None, None        # 0 na testovacím proužku = negativní
    return {"analyte": analyte, "value": value, "value_text": text, "unit": unit,
            "ref_low": lo, "ref_high": hi, "note": note, "taken_on": taken_on}


def _value_unit(text: str) -> tuple[float | None, str, str, str]:
    """Z „5,4 mmol/L  L | |*| |“ udělá (číslo, text, jednotka, předpona <>)."""
    parts = text.split()
    m = _NUM.fullmatch(parts[0])
    if not m:                                   # kvalitativní výsledek: neg, +, G1
        return None, parts[0], "", ""
    unit = parts[1] if len(parts) > 1 and parts[1] not in {"L", "|"} and not parts[1].startswith("|") else ""
    return _f(m.group(2)), "", unit.strip("|"), m.group(1)


def _unique(name: str, seen: set) -> str:
    out, n = name, 2
    while out in seen:                          # nikdy nepřepsat dva různé řádky se stejným názvem
        out, n = f"{name} ({n})", n + 1
    seen.add(out)
    return out


def _finish(results: list, unparsed: list, lab: str) -> dict:
    if not results:
        return {"taken_on": None, "lab": lab, "results": [], "unparsed_rows": unparsed}
    taken_on = Counter(r["taken_on"] for r in results).most_common(1)[0][0]
    return {"taken_on": taken_on, "lab": lab, "results": results, "unparsed_rows": unparsed}


# ---------- A a B: SmartMEDIX ----------

def _parse_smartmedix(text: str) -> dict:
    m = _DATE_LABEL.search(text)
    if not m:
        raise LabParseError("V dokumentu jsem nenašel datum odběru")
    taken_on = _iso(m.group(1), m.group(2), m.group(3))
    lab = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    results, unparsed, seen = [], [], set()
    for line in text.splitlines():
        row = _A_ROW.match(line)
        if not row:
            continue
        code, name, rest = row["code"], row["name"].strip(), row["rest"].strip()
        lo = hi = None
        b = _REF_B.match(rest)
        if b and ("/" in rest.split()[0] or rest.startswith("/") or re.match(r"^[<>]?\d+(?:,\d+)?\s*/", rest)):
            lo, hi, rest = _f(b["lo"]), _f(b["hi"]), b["rest"].strip()        # rozložení B
        else:
            a = _REF_A.search(rest)
            if a:                                                              # rozložení A
                lo, hi, rest = _f(a["lo"]), _f(a["hi"]), rest[:a.start()].strip()
        if not rest:
            unparsed.append(line.strip()); continue
        value, value_text, unit, prefix = _value_unit(rest)
        if value is None and not value_text:
            unparsed.append(line.strip()); continue
        results.append(_result(_unique(MOC.get(code, name), seen), value, value_text, unit, lo, hi, prefix, taken_on))
    return {"taken_on": taken_on, "lab": lab, "results": results, "unparsed_rows": unparsed}


# ---------- C: EUC Lab „Opis výsledku“ ----------

def _parse_euc(text: str) -> dict:
    lab_m = re.search(r"Provádějící laboratoř:\s*\d*\s*(.+)", text)
    lab = lab_m.group(1).strip() if lab_m else "EUC Lab"
    results, unparsed, seen = [], [], set()
    for line in text.splitlines():
        row = _C_ROW.match(line)
        if not row:
            if re.match(r"^\s*\d{2}\.\d{2}\.\d{4}\s+\S+\s+\d{5}\s", line):
                unparsed.append(line.strip())
            continue
        d, m, y = row["d"].split(".")
        taken_on = _iso(d, m, y)
        parts = re.split(r"\s{2,}", row["rest"].strip())
        value_tok = parts[0]
        if value_tok.startswith("("):                                       # např. „Krevní obraz (provedeno)“ není výsledek
            continue
        unit, lo, hi = "", None, None
        for part in parts[1:]:
            ref = _REF.match(part)                                         # rozmezí, za ním může být komentář laboratoře
            if ref and (ref.end() == len(part) or part[ref.end()] == " "):
                lo, hi = _f(ref.group(1)), _f(ref.group(2))
            elif not unit and lo is None:
                unit = part
        num = _NUM.fullmatch(value_tok)
        value, vtext, prefix = (_f(num.group(2)), "", num.group(1)) if num else (None, " ".join([value_tok] + ([unit] if unit and not parts[1:] else [])), "")
        if not num:
            vtext, unit = " ".join(parts[:1]), ""
        name = MOC.get(row["code"], row["name"].strip())
        results.append(_result(_unique(name, seen), value, vtext, unit, lo, hi, prefix, taken_on))
    return _finish(results, unparsed, lab)


# ---------- D: Unilabs ----------

def _parse_unilabs(text: str) -> dict:
    m = _DATE_LABEL.search(text)
    if not m:
        raise LabParseError("V dokumentu jsem nenašel datum odběru")
    taken_on = _iso(m.group(1), m.group(2), m.group(3))
    lab_m = re.search(r"(Unilabs[^\n]*?k\.s\.)", text)
    lab = lab_m.group(1).strip() if lab_m else "Unilabs"
    results, unparsed, seen = [], [], set()
    for line in text.splitlines():
        ckd = re.match(r"^\s*Hodnocení stádia CKD.*?\.\.\.\s*(G\d\w*)\s*$", line)
        if ckd:
            results.append(_result(_unique("Hodnocení CKD dle eGFR", seen), None, ckd.group(1), "", None, None, "", taken_on))
            continue
        row = _D_ROW.match(line)
        if not row:
            continue
        parts = re.split(r"\s{2,}", row["rest"].strip())
        if len(parts) < 2:
            unparsed.append(line.strip()); continue
        raw_name, value_tok, unit, lo, hi = parts[0].strip(), parts[1], "", None, None
        for part in parts[2:]:
            ref = _REF.fullmatch(part)
            if ref:
                lo, hi = _f(ref.group(1)), _f(ref.group(2))
            elif part.startswith("|"):
                continue
            elif not unit:
                unit = part
        name = UNILABS_MOC.get(raw_name, f"{raw_name} (moč)") if row["tissue"].upper() == "U" else UNILABS_ALIASES.get(raw_name, raw_name)
        num = _NUM.fullmatch(value_tok)
        value, vtext, prefix = (_f(num.group(2)), "", num.group(1)) if num else (None, value_tok, "")
        results.append(_result(_unique(name, seen), value, vtext, unit if num else "", lo, hi, prefix, taken_on))
    return _finish(results, unparsed, lab) | {"taken_on": taken_on}


def parse_lab_text(text: str) -> dict:
    """Rozpozná rozložení a vrátí {taken_on, lab, results[{…, taken_on}], unparsed_rows}."""
    text = unicodedata.normalize("NFC", text)       # laboratoře zapisují diakritiku různě (ř = r + háček), názvy se musí shodovat
    if "Unilabs" in text and re.search(r"^\s*[A-Z]{2,5}(?:\s+AM)?\s+[A-Za-z]{1,2}_", text, re.M):
        return _parse_unilabs(text)
    if "NČLP" in text or "Opis výsledku" in text:
        parsed = _parse_euc(text)
        if not parsed["results"]:
            raise LabParseError("V dokumentu jsem nenašel datum odběru")
        return parsed
    return _parse_smartmedix(text)
