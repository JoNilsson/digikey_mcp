"""Sessions, decisions, revised BOMs, cache, quota and resume, reports and MCP tools."""

from __future__ import annotations

import asyncio

import pytest
from resolve_fakes import product, resistor_10k, variation
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from digikey_mcp import resolve
from digikey_mcp.client import DigiKeyError
from digikey_mcp.resolve import report, session
from digikey_mcp.resolve.models import ResolveError

HEADER = "Reference,Qty,Value,Footprint,MPN,Manufacturer\n"
BOM = (
    HEADER
    + "R1 R2 R3,3,10k,R_0603_1608Metric,RC0603FR-0710KL,Yageo\n"
    + "U1,1,LM358,SOIC-8,LM358DR,TI\n"
    + "C1 C2,2,100nF,C_0603_1608Metric,,\n"
    + "R9,1,1k,R_0603_1608Metric,,\n"
)


def lm358(**kw):
    return product("LM358DR", "Texas Instruments", [variation("296-1014-1-ND", "Cut Tape (CT)", breaks=((1, 0.5), (100, 0.3)))], **kw)


def cap_candidates():
    return [
        product("CL10B104KB8NNNC", "Samsung Electro-Mechanics",
                [variation("1276-1000-1-ND", "Cut Tape (CT)", breaks=((1, 0.1), (100, 0.02)))],
                desc="CAP CER 0.1UF 50V X7R 0603", params={"Package / Case": "0603"}, category="Capacitors"),
        product("GRM188R71H104KA93D", "Murata Electronics",
                [variation("490-1532-1-ND", "Cut Tape (CT)", breaks=((1, 0.1), (100, 0.03)))],
                desc="CAP CER 0.1UF 50V X7R 0603", params={"Package / Case": "0603"}, category="Capacitors"),
    ]


@pytest.fixture
def loaded(fake):
    caps = cap_candidates()
    fake.add(resistor_10k(), lm358(), *caps)
    fake.searches["100nF 0603 capacitor"] = caps
    fake.searches["1kohm 0603 resistor"] = []
    return fake


def run(env, text=BOM, **kw):
    return session.resolve_bom(text, source_is_text=True, **env, **kw)


def by_id(bom, line_id):
    return next(ln for ln in bom.lines if ln.line.line_id == line_id)


def test_resolve_bom_statuses_and_totals(loaded, env):
    bom = run(env, boards=2, session_id="proj")
    assert bom.complete and bom.session_id == "proj"
    assert by_id(bom, 1).status == "resolved" and by_id(bom, 1).needed_qty == 6 + 2  # 6 base, +2 minimum overage
    assert by_id(bom, 2).status == "resolved" and by_id(bom, 2).needed_qty == 2
    assert by_id(bom, 3).status == "needs_decision" and by_id(bom, 3).needed_qty == 4 + 2
    assert by_id(bom, 4).status == "needs_decision" and by_id(bom, 4).candidates == []
    assert bom.counts == {"resolved": 2, "needs_decision": 2}
    assert bom.estimated_total == pytest.approx(8 * 0.10 + 2 * 0.5)  # 8 resistors are below the 10-piece break
    d = bom.to_dict()
    assert d["ready_for_cart"] is False and len(d["blocking"]) == 2
    assert all(ln.reason for ln in bom.lines)


def test_session_persists_and_decisions_survive(loaded, env):
    run(env, session_id="proj")
    bom = session.load_bom("proj", cache_dir=env["cache_dir"])  # a fresh load from disk
    assert by_id(bom, 3).status == "needs_decision"
    top = by_id(bom, 3).candidates[0]
    assert top.dk_pn in {"1276-1000-1-ND", "490-1532-1-ND"}

    bom = session.apply_decisions("proj", {"3": top.dk_pn, 4: "skip"}, **env)
    assert by_id(bom, 3).status == "resolved" and by_id(bom, 3).decided and by_id(bom, 3).dk_pn == top.dk_pn
    assert by_id(bom, 3).order_qty == 4  # 2 caps + overage 2
    assert by_id(bom, 4).status == "skipped"

    bom = session.load_bom("proj", cache_dir=env["cache_dir"])
    assert by_id(bom, 3).decided and by_id(bom, 4).status == "skipped"
    assert bom.complete and not bom.blocking

    items = session.cart_items("proj", cache_dir=env["cache_dir"])
    assert ("296-1014-1-ND", 1, "U1") in items
    assert any(cref == "C1,C2" for _, _, cref in items)
    assert any(cref == "R1,R2,R3" for _, _, cref in items)
    assert len(items) == 3  # the skipped line is not in the cart


def test_cart_items_exclude_blocking_lines(loaded, env):
    run(env, session_id="proj")
    summary = session.cart_items_summary("proj", cache_dir=env["cache_dir"])
    assert summary["item_count"] == 2 and summary["ready_for_cart"] is False
    assert {b["line_id"] for b in summary["blocking"]} == {3, 4}
    assert summary["items"][0] == {"digikey_part_number": "311-10.0KHRCT-ND", "quantity": 5, "customer_reference": "R1,R2,R3"}


def test_decision_with_unknown_line_or_dnp(loaded, env):
    run(env, session_id="proj")
    with pytest.raises(ResolveError, match="no line 99"):
        session.apply_decisions("proj", {"99": "skip"}, **env)
    with pytest.raises(ResolveError, match="unknown session"):
        session.apply_decisions("nope", {"1": "skip"}, **env)
    with pytest.raises(ResolveError, match="not a number"):
        session.apply_decisions("proj", {"abc": "skip"}, **env)


def test_decision_part_not_in_stock_is_unavailable_with_substitutes(loaded, env):
    run(env, session_id="proj")
    low = product("LOW", "Maker", [variation("LOW-ND", "Cut Tape (CT)", stock=1)])
    loaded.add(low)
    bom = session.apply_decisions("proj", {"3": "LOW-ND"}, **env)
    assert by_id(bom, 3).status == "unavailable" and by_id(bom, 3).decided
    assert bom.counts["unavailable"] == 1


def test_revised_bom_reuses_decisions_for_unchanged_lines(loaded, env):
    run(env, session_id="proj")
    pick = by_id(session.load_bom("proj", cache_dir=env["cache_dir"]), 3).candidates[1].dk_pn
    session.apply_decisions("proj", {"3": pick, "4": "skip"}, **env)

    revised = BOM.replace("U1,1,LM358", "U1,2,LM358") + "R10,1,4.7k,R_0805_2012Metric,,\n"
    # row order changes too, so line ids differ
    lines = revised.splitlines()
    revised = "\n".join([lines[0], lines[3], lines[4], lines[1], lines[2], lines[5]]) + "\n"
    calls_before = len(loaded.calls)
    bom = run(env, text=revised, session_id="proj")
    cap = next(ln for ln in bom.lines if ln.line.value == "100nF")
    assert cap.status == "resolved" and cap.decided and cap.dk_pn == pick
    one_k = next(ln for ln in bom.lines if ln.line.value == "1kohm")
    assert one_k.status == "skipped"
    new = next(ln for ln in bom.lines if ln.line.value == "4.7kohm")
    assert new.status == "needs_decision" and not new.decided
    assert next(ln for ln in bom.lines if ln.line.mpn == "LM358DR").needed_qty == 2
    assert len(loaded.calls) > calls_before


def test_unchanged_source_resumes_without_rework(loaded, env):
    run(env, session_id="proj")
    n = len(loaded.calls)
    bom = run(env, session_id="proj")
    assert len(loaded.calls) == n and bom.complete
    assert session.resolve_bom(session_id="proj", **env).complete  # resume with no source


def test_missing_source_and_unknown_session(loaded, env):
    with pytest.raises(ResolveError, match="source is required"):
        session.resolve_bom(**env)
    with pytest.raises(ResolveError, match="unknown session"):
        session.resolve_bom(session_id="ghost", **env)
    with pytest.raises(ResolveError, match="not found"):
        session.resolve_bom("/no/such/bom.csv", **env)
    with pytest.raises(ResolveError, match="session_id must be"):
        session.resolve_bom(BOM, source_is_text=True, session_id="../evil", **env)


def test_bom_file_path(loaded, env, tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(BOM)
    bom = session.resolve_bom(str(path), **env)
    assert bom.counts["resolved"] == 2
    assert bom.session_id.startswith("bom-")


# ---- cache -----------------------------------------------------------------


def test_cache_serves_repeats_and_expires(loaded, env, clock):
    run(env, session_id="a")
    n = len(loaded.calls)
    run(env, session_id="b")  # a different session, same parts
    assert len(loaded.calls) == n
    clock.now += 7 * 3600  # price and stock data expire after 6 hours
    run(env, session_id="c")
    assert len(loaded.calls) > n


def test_static_data_outlives_price_data(loaded, env, clock):
    from digikey_mcp.resolve.gateway import Gateway
    from digikey_mcp.resolve.prefs import Preferences
    from digikey_mcp.resolve.store import Store

    with Store(env["cache_dir"]) as store:
        gw = Gateway(loaded, store, Preferences(), clock=clock, sleep=clock.sleep)
        gw.alternate_packaging("X-ND")
        gw.substitutions("X-ND")
        clock.now += 12 * 3600
        gw.alternate_packaging("X-ND")
        gw.substitutions("X-ND")
    assert loaded.names().count("alternate_packaging") == 1
    assert loaded.names().count("substitutions") == 2


# ---- quota -----------------------------------------------------------------


def quota_prefs(tmp_path, **quota):
    path = tmp_path / "prefs.toml"
    body = "\n".join(f"{k} = {v}" for k, v in quota.items())
    path.write_text(f"[quota]\nreserve = 0\n{body}\n")
    return str(path)


MPN_BOM = HEADER + "".join(f"U{i},1,IC{i},SOIC-8,PART{i},Maker\n" for i in range(1, 6))


@pytest.fixture
def five_parts(fake):
    for i in range(1, 6):
        fake.add(product(f"PART{i}", "Maker", [variation(f"PART{i}-ND", "Cut Tape (CT)")]))
    return fake


def test_minute_quota_exhausted_returns_partial_and_resumes(five_parts, env, clock, tmp_path):
    prefs = quota_prefs(tmp_path, per_minute=3, max_sleep_seconds=0)
    bom = run(env, MPN_BOM, preferences_path=prefs, session_id="q")
    assert not bom.complete
    assert bom.counts == {"resolved": 3, "pending": 2}
    assert "quota exhausted (minute): 3 of 5 lines done, resume with session_id 'q'" in bom.stopped
    assert bom.to_dict()["stopped"] and bom.to_dict()["ready_for_cart"] is False
    assert len(five_parts.calls) == 3  # no failing call was made

    clock.now += 61
    bom = session.resolve_bom(session_id="q", **env)
    assert bom.complete and bom.counts == {"resolved": 5}
    assert len(five_parts.calls) == 5


def test_minute_quota_sleeps_when_allowed(five_parts, env, clock, tmp_path):
    prefs = quota_prefs(tmp_path, per_minute=3, max_sleep_seconds=65, time_budget_seconds=1000)
    bom = run(env, MPN_BOM, preferences_path=prefs)
    assert bom.complete and bom.counts == {"resolved": 5}
    assert clock.slept and 0 < clock.slept[0] <= 60


def test_daily_quota_is_persisted_across_runs(five_parts, env, clock, tmp_path):
    prefs = quota_prefs(tmp_path, per_day=4, per_minute=100)
    bom = run(env, MPN_BOM, preferences_path=prefs, session_id="d")
    assert bom.counts == {"resolved": 4, "pending": 1}
    assert "quota exhausted (day)" in bom.stopped
    status = session.quota_status("d", cache_dir=env["cache_dir"], clock=clock)
    assert status["used_today_utc"] == 4 and status["remaining_today"] == 0
    still = session.resolve_bom(session_id="d", **env)  # same day: still stopped, no call made
    assert not still.complete and len(five_parts.calls) == 4
    clock.now += 86400
    assert session.resolve_bom(session_id="d", **env).complete


def test_quota_status_reports_usage(five_parts, env, clock):
    run(env, MPN_BOM)
    s = session.quota_status(cache_dir=env["cache_dir"], clock=clock)
    assert s["used_last_minute"] == 5 and s["remaining_this_minute"] == 110 and s["remaining_today"] == 990
    clock.now += 61
    assert session.quota_status(cache_dir=env["cache_dir"], clock=clock)["used_last_minute"] == 0


def test_digikey_403_stops_the_run(five_parts, env, clock):
    five_parts.fail_with = DigiKeyError("suspended", status=403)
    bom = run(env, MPN_BOM, session_id="s")
    assert not bom.complete and "quota exhausted (suspended)" in bom.stopped
    assert len(five_parts.calls) == 1
    five_parts.fail_with = None
    still = session.resolve_bom(session_id="s", **env)
    assert not still.complete and len(five_parts.calls) == 1  # held off for the suspension
    clock.now += 601
    assert session.resolve_bom(session_id="s", **env).complete


def test_not_found_is_cached_and_costs_no_second_call(fake, env):
    run(env, HEADER + "U1,1,IC,SOIC-8,GHOST,Maker\n", session_id="g")
    n = len(fake.calls)
    run(env, HEADER + "U1,1,IC,SOIC-8,GHOST,Maker\n", session_id="g2")
    assert len(fake.calls) == n


# ---- reports and tools -------------------------------------------------------


def test_markdown_and_csv_report(loaded, env, tmp_path):
    bom = run(env, session_id="proj")
    md = report.to_markdown(bom)
    assert "| Line | Ref |" in md and "## Blocking lines" in md and "## Packaging comparison" in md
    assert "RC0603FR-0710KL" in md and "needs_decision" in md and "break-even" in md
    csv_text = report.to_csv(bom)
    header, *rows = csv_text.splitlines()
    assert header.startswith("line,refdes") and len(rows) == 4
    out = tmp_path / "bom.csv"
    assert report.write_report(csv_text, str(out), (".csv",)) == str(out) and out.read_text() == csv_text
    with pytest.raises(ValueError, match="must end in"):
        report.write_report(csv_text, str(tmp_path / "bom.txt"), (".csv",))
    with pytest.raises(ValueError, match="does not exist"):
        report.write_report(csv_text, str(tmp_path / "nodir" / "x.csv"), (".csv",))
    with pytest.raises(ValueError, match="directory"):
        report.write_report(csv_text, str(tmp_path), (".csv", ""))


@pytest.fixture
def server(loaded, monkeypatch, cache):
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache.parent / "xdg"))
    mcp = FastMCP("t")
    resolve.register(mcp, client_factory=lambda: loaded)
    return mcp


def call(server, tool, **args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go()).data


def test_tools_registered_and_end_to_end(server, tmp_path):
    async def names():
        async with Client(server) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) == {"resolve_bom", "apply_decisions", "bom_report", "cart_items_for_session", "quota_status"}
    result = call(server, "resolve_bom", source=BOM, source_is_text=True, boards=1, session_id="t1")
    assert result["session_id"] == "t1" and result["counts"] == {"resolved": 2, "needs_decision": 2}
    pn = next(ln for ln in result["lines"] if ln["line_id"] == 3)["candidates"][0]["dk_pn"]
    done = call(server, "apply_decisions", session_id="t1", decisions={"3": pn, "4": "skip"})
    assert done["ready_for_cart"] is True
    cart = call(server, "cart_items_for_session", session_id="t1")
    assert cart["item_count"] == 3 and {"digikey_part_number", "quantity", "customer_reference"} == set(cart["items"][0])
    out = tmp_path / "r.csv"
    rep = call(server, "bom_report", session_id="t1", format="csv", write_to=str(out))
    assert rep["written_to"] == str(out) and out.exists()
    assert "| Line |" in call(server, "bom_report", session_id="t1")["report"]
    assert call(server, "quota_status")["used_last_minute"] > 0


def test_tool_errors_are_clean(server):
    with pytest.raises(ToolError, match="unknown session"):
        call(server, "cart_items_for_session", session_id="missing")
    call(server, "resolve_bom", source=BOM, source_is_text=True, session_id="t2")
    with pytest.raises(ToolError, match="must end in"):
        call(server, "bom_report", session_id="t2", write_to="/tmp/x.txt")
