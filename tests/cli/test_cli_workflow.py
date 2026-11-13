"""resolve -> decide -> report -> cart round trip, exit codes and clean JSON, all offline."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from conftest import BOM, CAP_PN

from digikey_mcp.cli import common


def session_id_of(stderr: str) -> str:
    return next(line.split("session ")[1].split(" ")[0] for line in stderr.splitlines() if "session " in line)


def test_round_trip_resolve_decide_report_cart(fake, run, tmp_path):
    code, out, err = run("resolve", "--text", BOM, "--session", "proj", "--boards", "2")
    assert code == 3, err
    assert "needs_decision" in out and "RC0603FR-0710KL" not in err
    assert "Session: proj" in err and "decide proj --interactive" in err

    code, out, err = run("decide", "proj", f"3={CAP_PN}", "4=skip")
    assert code == 0, err
    assert CAP_PN in out and "skipped" in out

    code, out, err = run("report", "proj", "--format", "csv", "--out", str(tmp_path / "bom.csv"))
    assert code == 0 and out == ""
    csv_text = (tmp_path / "bom.csv").read_text()
    assert CAP_PN in csv_text and "311-10.0KHRCT-ND" in csv_text
    code, out, _ = run("report", "proj")
    assert out.startswith("# BOM resolution proj") and "Estimated total" in out

    code, out, err = run("cart", "proj")
    assert code == 0, err
    assert "Nothing has been submitted" in err
    urls = out.splitlines()
    assert urls and urls[0].startswith("https://www.digikey.com/classic/ordering/fastadd.aspx?newcart=true")
    query = parse_qs(urlsplit(urls[0]).query)
    assert CAP_PN in [v[0] for k, v in query.items() if k.startswith("part")]

    code, out, err = run("cart", "proj", "--no-newcart")
    assert "newcart" not in out and code == 0
    code, out, err = run("cart", "proj", "--output", "html", "--out", str(tmp_path / "cart.html"))
    assert code == 0 and "<form" in (tmp_path / "cart.html").read_text()
    code, out, err = run("cart", "proj", "--output", "text")
    assert CAP_PN in out


def test_resolve_json_keeps_stdout_clean(fake, run):
    code, out, err = run("resolve", "--text", BOM, "--session", "j", "--json")
    assert code == 3
    data = json.loads(out)
    assert data["session_id"] == "j" and data["counts"]["needs_decision"] == 2
    assert "Resolving" in err  # progress went to stderr


def test_resolve_all_resolved_exits_zero(fake, run):
    text = "Reference,Qty,Value,Footprint,MPN,Manufacturer\nU1,1,LM358,SOIC-8,LM358DR,TI\n"
    code, out, err = run("resolve", "--text", text, "--session", "ok")
    assert code == 0, err
    assert "296-1014-1-ND" in out
    code, out, err = run("-q", "resolve", "--text", text, "--session", "ok2")
    assert code == 0 and "Next:" not in err and "Resolving" not in err


def test_resolve_reads_file_and_stdin(fake, run, bom_file, monkeypatch):
    import io

    code, out, _ = run("resolve", str(bom_file), "--json")
    assert code == 3 and json.loads(out)["line_count"] == 4
    monkeypatch.setattr("sys.stdin", io.StringIO(BOM))
    code, out, _ = run("resolve", "-", "--json", "--session", "fromstdin")
    assert code == 3 and json.loads(out)["session_id"] == "fromstdin"


def test_quota_exhausted_exits_3_with_resume_command(fake, run, tmp_path):
    prefs = tmp_path / "prefs.toml"
    prefs.write_text("[quota]\nreserve = 0\nper_day = 1\n")
    code, out, err = run("resolve", "--text", BOM, "--session", "q", "--prefs", str(prefs))
    assert code == 3
    assert "quota exhausted" in err
    assert "Session: q" in err and "resolve --session q" in err
    data = json.loads(run("report", "q", "--format", "json")[1])
    assert data["complete"] is False


def test_decide_interactive_walks_open_lines(fake, run, monkeypatch):
    run("resolve", "--text", BOM, "--session", "ia")
    answers = iter(["9", "1", "s"])
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    code, out, err = run("decide", "ia", "--interactive")
    assert code == 0, err
    assert "1) " + CAP_PN in out or CAP_PN in out
    assert "no option 9" in out
    assert "skipped" in out
    assert len(prompts) == 3


def test_decide_interactive_can_leave_and_quit(fake, run, monkeypatch):
    run("resolve", "--text", BOM, "--session", "lq")
    answers = iter(["", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code, out, _ = run("decide", "lq", "--interactive")
    assert code == 3
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    assert run("decide", "lq", "--interactive")[0] == 3


def test_decide_a_part_number_not_in_candidates(fake, run):
    run("resolve", "--text", BOM, "--session", "pn")
    code, out, _ = run("decide", "pn", "3=311-10.0KHRCT-ND", "4=skip")
    assert code == 0
    assert "311-10.0KHRCT-ND" in out


def test_sessions_and_quota(fake, run):
    assert run("sessions")[1].startswith("No saved sessions")
    run("resolve", "--text", BOM, "--session", "one")
    code, out, _ = run("sessions")
    assert code == 0 and "one" in out and "needs decisions" in out
    rows = json.loads(run("sessions", "--json")[1])
    assert rows[0]["session_id"] == "one" and rows[0]["counts"]["needs_decision"] == 2
    assert json.loads(run("quota", "--json")[1])["used_today_utc"] >= 1
    assert "per minute" in run("quota", "one")[1]


def test_ingest_report_and_json(run, bom_file):
    code, out, _ = run("ingest", str(bom_file))
    assert code == 0
    for text in ("Column mapping", "Reference", "refdes", "generic"):
        assert text in out
    code, out, err = run("ingest", str(bom_file), "--json")
    data = json.loads(out)
    assert code == 0 and len(data["lines"]) == 4 and data["mapping_report"]["mapped"]["Reference"] == "refdes"


def test_ingest_map_override_and_stdin(run, tmp_path, monkeypatch):
    path = tmp_path / "odd.csv"
    path.write_text("Teilenummer,Ref,Menge\nLM358DR,U1,1\n")
    code, out, _ = run("ingest", str(path), "--map", "mpn=Teilenummer", "--json")
    assert code == 0
    assert json.loads(out)["lines"][0]["mpn"] == "LM358DR"
    code, out, err = run("ingest", str(path), "--map", "bogus=Ref")
    assert code == 2 and "unknown field" in err
    assert run("ingest", str(path), "--map", "noequals")[0] == 2
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("R1, R2 10k 0603\n"))
    assert run("ingest", "-", "--format", "text", "--json")[0] == 0


def test_search_and_part_use_the_mcp_tool_functions(fake, run):
    code, out, _ = run("search", "RC0603FR-0710KL", "--limit", "3", "--in-stock")
    assert code == 0 and "311-10.0KHRCT-ND" in out
    code, out, _ = run("search", "LM358DR", "--json")
    assert json.loads(out)["exact_matches"][0]["manufacturer_part_number"] == "LM358DR"
    code, out, _ = run("part", "311-10.0KHRCT-ND")
    assert code == 0 and "Packaging" in out and "YAGEO" in out
    code, out, _ = run("part", "311-10.0KHRCT-ND", "--json")
    assert json.loads(out)["manufacturer_part_number"] == "RC0603FR-0710KL"
    code, out, _ = run("part", "311-10.0KHRCT-ND", "--raw")
    assert "Product" in json.loads(out)
    assert run("search", "x", "--limit", "99")[0] == 2


class TestExitCodes:
    def test_usage_errors_are_2(self, run):
        for argv in (["bogus"], ["resolve"], ["decide", "s"], ["decide", "s", "x"], ["decide", "s", "a=b"],
                     ["report", "s", "--format", "pdf"], ["resolve", "--boards", "zero", "x"],
                     ["cart"], ["search"]):
            code, out, err = run(*argv)
            assert code == 2, argv
            assert err.startswith("digikey-mcp: error: ") and "Traceback" not in err

    def test_runtime_errors_are_1_one_line_plus_hint(self, run, fake):
        for argv in (["report", "missing"], ["cart", "missing"], ["quota", "missing"],
                     ["ingest", "/no/such/file.csv"], ["resolve", "--session", "nope"]):
            code, out, err = run(*argv)
            assert code == 1, argv
            lines = [ln for ln in err.strip().splitlines() if not ln.startswith("Resolving")]
            assert lines[0].startswith("digikey-mcp: error: ") and len(lines) <= 2, argv
        assert "digikey-mcp sessions" in run("report", "missing")[2]

    def test_missing_credentials_name_the_variables(self, run, monkeypatch):
        for var in ("CLIENT_ID", "CLIENT_SECRET", "CLIENT_ID_FILE", "CLIENT_SECRET_FILE"):
            monkeypatch.delenv(var, raising=False)
        code, out, err = run("search", "LM358")
        assert code == 1
        assert "CLIENT_ID" in err and "CLIENT_ID_FILE" in err and "CLIENT_SECRET_FILE" in err
        assert out == ""

    def test_digikey_api_error_is_one_line(self, fake, run):
        from digikey_mcp.client import DigiKeyError

        fake.fail_with = DigiKeyError("DigiKey says no", status=500)
        code, out, err = run("part", "311-10.0KHRCT-ND")
        assert code == 1 and "DigiKey says no" in err

    def test_cart_with_nothing_resolved_is_an_error(self, fake, run):
        run("resolve", "--text", "Reference,Qty,Value,Footprint\nR9,1,1k,R_0603\n", "--session", "empty")
        code, out, err = run("cart", "empty")
        assert code == 1 and "no resolved lines" in err

    def test_report_bad_extension(self, fake, run, tmp_path):
        run("resolve", "--text", BOM, "--session", "r")
        code, _, err = run("report", "r", "--out", str(tmp_path / "x.txt"))
        assert code == 1 and "--out" in err


def test_cart_lists_blocking_lines_but_still_builds(fake, run):
    run("resolve", "--text", BOM, "--session", "partial")
    code, out, err = run("cart", "partial")
    assert code == 0
    assert "2 line(s) are not in the cart" in err
    assert "311-10.0KHRCT-ND" in out


def test_client_is_only_built_when_needed(monkeypatch, run, tmp_path):
    def boom():
        raise AssertionError("no DigiKey client expected")

    monkeypatch.setattr(common, "get_client", boom)
    assert run("sessions")[0] == 0
    assert run("quota")[0] == 0
    assert run("ingest", str(_write(tmp_path)))[0] == 0


def _write(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text(BOM)
    return path
