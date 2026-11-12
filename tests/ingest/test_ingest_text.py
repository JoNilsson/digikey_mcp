import pytest

from digikey_mcp.ingest import ingest


def test_tab_separated_paste():
    text = "Ref\tValue\tFootprint\tQty\tMPN\nR1, R2\t10k\t0603\t2\tRC0603FR-0710KL\nC1\t100nF\t0603\t1\t\n"
    res = ingest(text)
    assert res.fmt == "csv"
    r, c = res.lines
    assert r.refdes == ["R1", "R2"] and r.mpn == "RC0603FR-0710KL" and r.quantity == 2
    assert c.generic and c.value == "100nF"


def test_pipe_table_with_markdown_separator():
    text = (
        "| Reference | Value | Qty |\n"
        "|-----------|-------|-----|\n"
        "| R1-R2     | 4k7   | 2   |\n"
        "| C1        | 0.1uF | 1   |\n"
    )
    res = ingest(text)
    assert [(ln.refdes, ln.value, ln.quantity) for ln in res.lines] == [
        (["R1", "R2"], "4.7kohm", 2),
        (["C1"], "100nF", 1),
    ]


def test_space_aligned_columns_with_unparseable_row_reported():
    text = (
        "Designator  Value   Footprint  Qty\n"
        "R1-R4       10k     0603       4\n"
        "C1          100nF   0603       1\n"
        "see sheet 2 for connectors\n"
    )
    res = ingest(text)
    assert res.fmt == "text"
    assert [ln.refdes for ln in res.lines] == [["R1", "R2", "R3", "R4"], ["C1"]]
    assert len(res.unparsed_rows) == 1
    assert res.unparsed_rows[0].source_row == 4
    assert "columns" in res.unparsed_rows[0].reason


def test_single_space_columns():
    res = ingest("Ref Value Qty\nR1 10k 1\nC1 100nF 2\n")
    assert [(ln.refdes, ln.value, ln.quantity) for ln in res.lines] == [
        (["R1"], "10kohm", 1),
        (["C1"], "100nF", 2),
    ]


def test_headerless_lines_are_parsed_conservatively():
    text = (
        "Bill of materials for rev B\n"
        "R1, R2  10k 0603\n"
        "C1-C3 100nF 0603 x3\n"
        "U1 LM358 SOIC-8 296-8875-1-ND\n"
        "4x 22pF 0402\n"
        "R9 10\n"
        "lorem ipsum dolor\n"
    )
    res = ingest(text)
    assert res.fmt == "text"
    by = {tuple(ln.refdes): ln for ln in res.lines}
    r = by[("R1", "R2")]
    assert (r.value, r.footprint, r.quantity, r.generic) == ("10kohm", "0603", 2, True)
    assert any("free text" in n for n in r.notes)
    c = by[("C1", "C2", "C3")]
    assert c.value == "100nF" and c.quantity == 3
    u = by[("U1",)]
    assert u.supplier_pn == "296-8875-1-ND" and not u.generic
    q = by[()]
    assert (q.quantity, q.value, q.footprint) == (4, "22pF", "0402")
    assert {x.text for x in res.unparsed_rows} == {
        "Bill of materials for rev B",
        "R9 10",
        "lorem ipsum dolor",
    }


def test_headerless_part_name_is_not_taken_as_designator():
    res = ingest("LM358 dual op amp 8-pin\n")
    assert res.lines == [] and len(res.unparsed_rows) == 1


def test_nothing_recognised_warns():
    res = ingest("hello world\n")
    assert res.lines == [] and res.warnings


def test_empty_input():
    res = ingest("")
    assert res.lines == [] and res.unparsed_rows == []


def test_pdf_text_goes_through_the_text_parser(monkeypatch):
    """PDF extraction is stubbed; the extracted text is parsed like pasted text."""
    from digikey_mcp.ingest import core

    monkeypatch.setattr(
        core,
        "read_pdf",
        lambda data, keys=(): (None, "Reference  Value  Qty\nR1  10k  1\n", []),
    )
    res = ingest(b"%PDF-1.4 stub")
    assert res.fmt == "pdf"
    assert res.lines[0].value == "10kohm"


def test_real_pdf_text_extraction(tmp_path):
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    path = tmp_path / "blank.pdf"
    with open(path, "wb") as fh:
        writer.write(fh)
    res = ingest(path)
    assert res.fmt == "pdf"
    assert res.lines == []
