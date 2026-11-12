import io

import pytest

from digikey_mcp.ingest import MissingDependencyError, ingest
from digikey_mcp.ingest.models import IngestError


def by_ref(result, ref):
    return next(ln for ln in result.lines if ref in ln.refdes)


# ------------------------------------------------------------------ KiCad CSV


def test_kicad_csv_without_part_numbers(fixtures_dir):
    res = ingest(fixtures_dir / "kicad_bom.csv")
    assert res.fmt == "csv"
    rep = res.mapping_report
    assert rep.mapped["Reference"] == "refdes"
    assert rep.mapped["Qty"] == "quantity"
    assert rep.mapped["DNP"] == "dnp"
    assert rep.unmapped == ["Datasheet"]
    assert rep.missing_fields == ["mpn"]
    assert res.unparsed_rows == []

    # C1,C2 and C3 (0.1uF == 100nF, same footprint) are one line
    caps = by_ref(res, "C1")
    assert caps.refdes == ["C1", "C2", "C3"]
    assert caps.quantity == 3
    assert caps.value == "100nF"
    assert caps.package == "0603"
    assert caps.category_hint == "capacitor"
    assert caps.generic

    r = by_ref(res, "R1")
    assert r.refdes == ["R1", "R2", "R3"] and r.quantity == 3 and r.value == "10kohm"
    assert r.value_raw == "10k"
    assert r.raw["Reference"] == "R1-R3"


def test_kicad_csv_surfaces_dnp_and_non_parts(fixtures_dir):
    res = ingest(fixtures_dir / "kicad_bom.csv")
    r4 = by_ref(res, "R4")
    assert r4.dnp and not r4.orderable
    assert r4.value == "4.7kohm"
    assert [ln.refdes for ln in res.dnp_lines] == [["R4"]]
    cats = {ln.category_hint for ln in res.non_part_lines}
    assert cats == {"fiducial", "mounting_hole", "test_point"}
    assert all(not ln.orderable for ln in res.non_part_lines)
    assert by_ref(res, "U1").orderable
    # nothing is dropped: 2 caps lines merged, every designator still present
    all_refs = {r for ln in res.lines for r in ln.refdes}
    assert all_refs == {"C1", "C2", "C3", "FID1", "FID2", "H1", "R1", "R2", "R3", "R4", "TP1", "U1"}


def test_kicad_csv_with_preamble_and_part_numbers(fixtures_dir):
    res = ingest(str(fixtures_dir / "kicad_bom_mpn.csv"))
    assert res.preamble[0].endswith("board.kicad_sch")
    assert res.mapping_report.header_row == 6
    assert res.unparsed_rows == []
    assert res.mapping_report.missing_fields == []
    r = by_ref(res, "R1")
    # three per-designator rows with one MPN become one line
    assert r.refdes == ["R1", "R2", "R3"]
    assert r.quantity == 3
    assert r.mpn == "RC0603FR-0710KL"
    assert r.manufacturer == "Yageo"
    assert r.supplier_pn == "311-10.0KHRCT-ND"
    assert r.source_rows == [7, 8, 9]
    assert not r.generic
    u = by_ref(res, "U1")
    assert u.mpn == "LM358DR" and u.supplier_pn is None and not u.generic


def test_distinct_mpns_are_not_merged():
    text = "Reference,Value,MPN\nR1,10k,AAA\nR2,10k,BBB\n"
    assert len(ingest(text).lines) == 2


def test_dnp_and_populated_rows_stay_separate():
    text = "Reference,Value,Footprint,DNP\nR1,10k,R_0603,\nR2,10k,R_0603,DNP\n"
    res = ingest(text)
    assert [(ln.refdes, ln.dnp) for ln in res.lines] == [(["R1"], False), (["R2"], True)]


# ------------------------------------------------------------------ KiCad XML


def test_kicad_xml(fixtures_dir):
    res = ingest(fixtures_dir / "kicad_netlist.xml")
    assert res.fmt == "xml"
    r = by_ref(res, "R1")
    assert r.refdes == ["R1", "R2"] and r.quantity == 2
    assert r.mpn == "RC0603FR-0710KL" and r.manufacturer == "Yageo"
    assert r.description == "Resistor"
    c = by_ref(res, "C1")
    assert c.value == "100nF" and c.generic
    r9 = by_ref(res, "R9")
    assert r9.dnp and r9.value == "0ohm"
    assert res.unparsed_rows == []


def test_xml_text_is_sniffed_without_extension(fixtures_dir):
    text = (fixtures_dir / "kicad_netlist.xml").read_text()
    assert ingest(text).fmt == "xml"
    assert ingest(text.encode()).fmt == "xml"


def test_generic_xml_records():
    xml = (
        "<bom><item><Designator>R1</Designator><Value>1k</Value><Qty>1</Qty></item>"
        "<item><Designator>C1</Designator><Value>10nF</Value><Qty>1</Qty></item></bom>"
    )
    res = ingest(xml)
    assert [(ln.refdes, ln.value) for ln in res.lines] == [(["R1"], "1kohm"), (["C1"], "10nF")]


def test_xml_with_entities_is_rejected():
    with pytest.raises(IngestError):
        ingest('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a><b/><b/></a>', fmt="xml")


# ---------------------------------------------------------------------- Altium


def test_altium_csv(fixtures_dir):
    res = ingest(fixtures_dir / "altium_bom.csv")
    rep = res.mapping_report
    assert rep.mapped["Comment"] == "value"
    assert rep.mapped["Designator"] == "refdes"
    assert rep.mapped["Manufacturer Part Number 1"] == "mpn"
    assert rep.mapped["Manufacturer 1"] == "manufacturer"
    assert set(rep.unmapped) == {"LibRef", "Supplier 1"}
    assert len(res.preamble) == 4

    caps = by_ref(res, "C2")
    assert caps.refdes == ["C1", "C2", "C3"] and caps.quantity == 3
    assert caps.supplier_pn == "1276-1000-1-ND"
    assert caps.description == "Capacitor, ceramic"
    res_line = by_ref(res, "R1")
    assert res_line.value == "10kohm" and res_line.tolerance == "1%"
    # a non-DigiKey supplier number is not reported as a DigiKey PN
    usb = by_ref(res, "J1")
    assert usb.supplier_pn is None and usb.generic and usb.category_hint == "connector"
    assert any("798-USB4105GFA" in n for n in usb.notes)


# ----------------------------------------------------------------------- Excel


def make_xlsx(rows, extra_sheet=False):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    for row in rows:
        ws.append(row)
    if extra_sheet:
        wb.create_sheet("Notes").append(["just a note"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_messy_excel_workbook(tmp_path):
    data = make_xlsx(
        [
            ["Acme Widget - Bill of Materials"],
            [None, None],
            ["Item", "Qty.", "Ref Des", "Mfr Part #", "Mfr", "Description", "Remarks"],
            [1, 3.0, "R1-R3", "RC0603FR-0710KL", "Yageo", "RES 10K 1% 0603", None],
            [2, 1, "C1", "CL10B104KB8NNNC", "Samsung", "CAP 0.1uF 0603", None],
            [3, 1, "R7", "RC0603FR-0710KL", "Yageo", "RES 10K 1% 0603", "DNP for rev A"],
            [None, None, None, None, None, None, None],
            ["Total", 5, None, None, None, None, None],
        ],
        extra_sheet=True,
    )
    path = tmp_path / "bom.xlsx"
    path.write_bytes(data)
    res = ingest(path)
    assert res.fmt == "xlsx"
    assert res.mapping_report.sheet == "BOM"
    assert res.mapping_report.header_row == 3
    assert res.mapping_report.mapped["Mfr Part #"] == "mpn"
    assert res.mapping_report.mapped["Qty."] == "quantity"
    assert res.preamble == ["row 1: Acme Widget - Bill of Materials"]
    assert any("Notes" in w for w in res.warnings)
    r = by_ref(res, "R1")
    assert r.refdes == ["R1", "R2", "R3"] and r.quantity == 3
    assert not r.dnp
    r7 = by_ref(res, "R7")
    assert r7.dnp and r7.mpn == "RC0603FR-0710KL"
    assert by_ref(res, "C1").mpn == "CL10B104KB8NNNC"
    # the totals row is reported, not guessed
    assert [(u.source_row, u.reason) for u in res.unparsed_rows] == [
        (8, "no designator, value or part number")
    ]


def test_excel_bytes_are_sniffed_without_extension():
    data = make_xlsx([["Reference", "Value", "Qty"], ["R1", "1k", 1]])
    res = ingest(data)
    assert res.fmt == "xlsx"
    assert res.lines[0].value == "1kohm"


# ------------------------------------------------------------ hand-built CSV


def test_free_form_hand_built_csv():
    text = (
        "Part,How many,Notes\n"
        "RC0603FR-0710KL,10,\n"
        "RC0603FR-0710KL,5,\n"
        "ATMEGA328P-AU,1,Do not populate\n"
    )
    res = ingest(text, mapping={"How many": "quantity"})
    assert res.fmt == "csv"
    a, b = res.lines
    assert (a.mpn, a.quantity, a.refdes) == ("RC0603FR-0710KL", 15, [])
    assert (b.mpn, b.dnp) == ("ATMEGA328P-AU", True)
    assert any("no designator column" in w for w in res.warnings)


def test_semicolon_delimited_and_latin1_bytes():
    raw = "Reference;Value;Qty\nR1;4k7;1\nC1;100µF;1\n".encode("latin-1")
    res = ingest(raw)
    assert [ln.value for ln in res.lines] == ["4.7kohm", "100uF"]
    assert any("Latin-1" in w or "1252" in w for w in res.warnings)


def test_quantity_without_refdes_uses_designator_count_and_flags_mismatch():
    res = ingest("Reference,Value,Qty\n\"R1, R2\",1k,5\nR3,2k,\n")
    a, b = res.lines
    assert a.quantity == 5 and any("differs" in n for n in a.notes)
    assert b.quantity == 1


def test_non_integer_and_text_quantity():
    res = ingest("Reference,Value,Qty\nR1,1k,2 pcs\nR2,2k,abc\n")
    assert res.lines[0].quantity == 2
    assert res.lines[1].quantity == 1 and any("not a number" in n for n in res.lines[1].notes)


def test_multiple_mpns_keep_first_and_note_the_rest():
    res = ingest("Reference,MPN\nR1,AAA-1; BBB-2\n")
    assert res.lines[0].mpn == "AAA-1"
    assert any("BBB-2" in n for n in res.lines[0].notes)


def test_digikey_pn_in_mpn_column_is_moved():
    res = ingest("Reference,Part Number\nR1,296-8875-1-ND\n")
    line = res.lines[0]
    assert line.supplier_pn == "296-8875-1-ND" and line.mpn is None and not line.generic


def test_placeholder_mpn_is_ignored():
    res = ingest("Reference,Value,MPN\nR1,1k,N/A\n")
    assert res.lines[0].mpn is None and res.lines[0].generic


def test_populate_column_is_inverted():
    res = ingest("Reference,Value,Populate\nR1,1k,yes\nR2,1k,no\n")
    assert [(ln.refdes[0], ln.dnp) for ln in res.lines] == [("R1", False), ("R2", True)]


def test_dnp_marker_in_value_and_designator_cells():
    res = ingest("Reference,Value\nR1,10k DNP\nC1 (DNF),100nF\nR2,not fitted\n")
    r1, c1, r2 = res.lines
    assert r1.dnp and r1.value == "10kohm" and r1.value_raw == "10k DNP"
    assert c1.dnp and c1.refdes == ["C1"]
    assert r2.dnp


def test_duplicate_designators_are_warned():
    res = ingest("Reference,Value\nR1,1k\nR1,2k\n")
    assert any("R1" in w for w in res.warnings)


def test_repeated_header_rows_are_not_lines():
    res = ingest("Reference,Value\nR1,1k\nReference,Value\nR2,2k\n")
    assert [ln.refdes for ln in res.lines] == [["R1"], ["R2"]]
    assert res.unparsed_rows == []


def test_csv_without_header_reports_instead_of_guessing():
    res = ingest("R1,10k\nR2,22k\n", fmt="csv")
    assert res.lines == []
    assert len(res.unparsed_rows) == 2
    assert res.warnings


def test_headerless_csv_with_index_mapping():
    res = ingest("R1,10k\nR2,22k\n", fmt="csv", mapping={0: "refdes", 1: "value"})
    assert [ln.value for ln in res.lines] == ["10kohm", "22kohm"]


# ----------------------------------------------------------------- API errors


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        ingest("definitely_missing_bom.csv")


def test_unknown_format_raises():
    with pytest.raises(ValueError):
        ingest("Reference,Value\nR1,1k\n", fmt="docx")


def test_legacy_xls_is_rejected(tmp_path):
    p = tmp_path / "old.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0 not really")
    with pytest.raises(IngestError):
        ingest(p)


def test_pdf_without_extractor_gives_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name in ("pdfplumber", "pypdf"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingDependencyError, match="pdfplumber"):
        ingest(b"%PDF-1.4 fake")


def test_xlsx_without_openpyxl_gives_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "openpyxl":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingDependencyError, match="openpyxl"):
        ingest(b"PK\x03\x04 fake")
