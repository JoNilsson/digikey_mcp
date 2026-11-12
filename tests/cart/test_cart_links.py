import asyncio
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from digikey_mcp import cart
from digikey_mcp.cart import (
    CartInputError,
    build_fastadd_form_html,
    build_fastadd_urls,
    build_quickorder_text,
)


def bom(n):
    return [(f"311-{i:05d}-ND", i % 7 + 1, f"R{i}") for i in range(1, n + 1)]


def query(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_single_chunk_format():
    (chunk,) = build_fastadd_urls([("1050-ABX00052-ND", 2, "cpn123"), ("311-10.0KHRCT-ND", 5, "")])
    assert chunk.url == (
        "https://www.digikey.com/classic/ordering/fastadd.aspx"
        "?newcart=true&part1=1050-ABX00052-ND&qty1=2&cref1=cpn123&part2=311-10.0KHRCT-ND&qty2=5"
    )
    assert (chunk.first_line, chunk.last_line, chunk.length) == (1, 2, len(chunk.url))


def test_empty_bom_gives_no_chunks():
    assert build_fastadd_urls([]) == []


def test_chunks_respect_limit_cover_all_lines_in_order():
    lines = bom(120)
    chunks = build_fastadd_urls(lines, max_url_len=400)
    assert len(chunks) > 1
    assert all(c.length == len(c.url) <= 400 for c in chunks)
    assert chunks[0].first_line == 1 and chunks[-1].last_line == 120
    for a, b in zip(chunks, chunks[1:]):
        assert b.first_line == a.last_line + 1
    seen = []
    for c in chunks:
        q = query(c.url)
        n = c.last_line - c.first_line + 1
        assert [q[f"part{i}"][0] for i in range(1, n + 1)] == [p for p, _, _ in lines[c.first_line - 1 : c.last_line]]
        assert f"part{n + 1}" not in q
        seen.extend(q[f"part{i}"][0] for i in range(1, n + 1))
    assert seen == [p for p, _, _ in lines]


def test_chunk_boundary_is_exact():
    lines = [("AAA-ND", 1, ""), ("BBB-ND", 1, "")]
    both = build_fastadd_urls(lines, newcart=False)[0]
    assert len(build_fastadd_urls(lines, newcart=False, max_url_len=both.length)) == 1
    split = build_fastadd_urls(lines, newcart=False, max_url_len=both.length - 1)
    assert [(c.first_line, c.last_line) for c in split] == [(1, 1), (2, 2)]
    assert query(split[1].url)["part1"] == ["BBB-ND"]


def test_single_line_too_long_names_the_line():
    lines = [("A-ND", 1, ""), ("B" * 90, 1, "x" * 50)]
    with pytest.raises(CartInputError, match="line 2"):
        build_fastadd_urls(lines, max_url_len=90)
    with pytest.raises(CartInputError, match="line 1"):
        build_fastadd_urls(lines, max_url_len=60)


def test_newcart_only_on_first_chunk():
    chunks = build_fastadd_urls(bom(60), max_url_len=500)
    assert len(chunks) > 2
    assert query(chunks[0].url)["newcart"] == ["true"]
    assert all("newcart" not in c.url for c in chunks[1:])


def test_newcart_false_omits_it_everywhere():
    chunks = build_fastadd_urls(bom(60), newcart=False, max_url_len=500)
    assert all("newcart" not in c.url for c in chunks)


def test_utm_source_on_every_chunk_encoded():
    chunks = build_fastadd_urls(bom(60), utm_source="my tool&x", max_url_len=500)
    assert len(chunks) > 1
    assert all(query(c.url)["utm_source"] == ["my tool&x"] for c in chunks)
    assert all(c.url.endswith("utm_source=my%20tool%26x") for c in chunks)


@pytest.mark.parametrize(
    "pn, encoded",
    [
        ("A/B-ND", "A%2FB-ND"),
        ("A B-ND", "A%20B-ND"),
        ("A#1-ND", "A%231-ND"),
        ("A+B-ND", "A%2BB-ND"),
        ("A&B=C-ND", "A%26B%3DC-ND"),
        ("100%-ND", "100%25-ND"),
        ("µF-ND", "%C2%B5F-ND"),
        ("Ω-ND", "%E2%84%A6-ND"),
    ],
)
def test_tricky_part_numbers_are_encoded(pn, encoded):
    (chunk,) = build_fastadd_urls([(pn, 1, pn)], newcart=False)
    assert f"part1={encoded}&" in chunk.url
    assert f"cref1={encoded}" in chunk.url
    q = query(chunk.url)
    assert q["part1"] == [pn] and q["cref1"] == [pn]
    assert chunk.url.isascii()
    assert "#" not in chunk.url and "+" not in chunk.url.replace("%2B", "")


def test_control_characters_are_stripped():
    (chunk,) = build_fastadd_urls([("A\x00B\r\n-ND\x7f", 1, "ref\x1b[31m\tone​")], newcart=False)
    q = query(chunk.url)
    assert q["part1"] == ["AB -ND"]
    assert q["cref1"] == ["ref[31m one"]
    assert not re.search(r"%0[0-9A-F]|%1[0-9A-F]|%7F", chunk.url)


def test_cref_is_capped():
    (chunk,) = build_fastadd_urls([("A-ND", 1, "x" * 500)], newcart=False)
    assert query(chunk.url)["cref1"] == ["x" * cart.fastadd.MAX_CREF_LEN]


@pytest.mark.parametrize("pn", ["", "   ", "\x00\t", None])
def test_empty_part_number_names_the_line(pn):
    with pytest.raises(CartInputError, match="line 3: empty part number"):
        build_fastadd_urls([("A-ND", 1, ""), ("B-ND", 1, ""), (pn, 1, "")])


@pytest.mark.parametrize("qty", [0, -1, 1.5, 2.0, True, None, "", "abc", "1e3", "-4", 10**9])
def test_bad_quantity_names_the_line(qty):
    with pytest.raises(CartInputError, match="line 2"):
        build_fastadd_urls([("A-ND", 1, ""), ("B-ND", qty, "")])


def test_quantity_digit_string_accepted():
    (chunk,) = build_fastadd_urls([("A-ND", "12", "")], newcart=False)
    assert query(chunk.url)["qty1"] == ["12"]


def test_malformed_item_names_the_line():
    with pytest.raises(CartInputError, match="line 2"):
        build_fastadd_urls([("A-ND", 1), "B-ND"])
    with pytest.raises(CartInputError, match="line 1"):
        build_fastadd_urls([("A-ND",)])


def test_mapping_items_accepted():
    (chunk,) = build_fastadd_urls(
        [{"digikey_part_number": "A-ND", "quantity": 3, "customer_reference": "r"}], newcart=False
    )
    assert chunk.url.endswith("?part1=A-ND&qty1=3&cref1=r")


def test_300_line_bom():
    lines = bom(300)
    chunks = build_fastadd_urls(lines, utm_source="bom-tool")
    assert all(c.length <= 1700 for c in chunks)
    assert 5 <= len(chunks) <= 20
    assert sum(c.last_line - c.first_line + 1 for c in chunks) == 300
    assert "newcart" in chunks[0].url and all("newcart" not in c.url for c in chunks[1:])
    parts = [p for c in chunks for p in (query(c.url)[f"part{i}"][0] for i in range(1, c.last_line - c.first_line + 2))]
    assert parts == [p for p, _, _ in lines]
    html = build_fastadd_form_html(lines)
    assert html.count("<form ") == 1 and html.count('name="part') == 300
    assert build_quickorder_text(lines).count("\n") == 300


def test_form_html_structure_and_no_autosubmit():
    html = build_fastadd_form_html(bom(5), utm_source="t")
    assert html.count("<form ") == 1
    assert 'method="post"' in html
    assert 'action="https://www.digikey.com/classic/ordering/fastadd.aspx"' in html
    assert '<button type="submit">' in html
    assert 'name="newcart" value="true"' in html
    assert 'name="part5" value="311-00005-ND"' in html
    assert 'name="utm_source" value="t"' in html
    low = html.lower()
    assert "<script" not in low and ".submit(" not in low and "onload" not in low
    assert "autofocus" not in low and 'http-equiv="refresh"' not in low


def test_form_html_splits_at_400_and_newcart_once():
    html = build_fastadd_form_html(bom(850))
    assert html.count("<form ") == 3
    assert html.count('<button type="submit">') == 3
    assert html.count('name="newcart"') == 1
    assert html.index('name="newcart"') < html.index("</form>")
    assert html.count('name="part400"') == 2 and html.count('name="part401"') == 0
    assert html.count('name="part50"') == 3
    assert "Add lines 401-800" in html and "Add lines 801-850" in html


def test_form_html_escapes_untrusted_values():
    html = build_fastadd_form_html([('"><script>alert(1)</script>', 1, "<b>&")])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&quot;&gt;" in html
    assert "&lt;b&gt;&amp;" in html


def test_form_html_rejects_bad_limit_and_bad_lines():
    with pytest.raises(CartInputError):
        build_fastadd_form_html(bom(2), max_parts_per_form=401)
    with pytest.raises(CartInputError, match="line 1"):
        build_fastadd_form_html([("", 1, "")])


def test_quickorder_text():
    text = build_quickorder_text([("A-ND", 2, "r1"), ("B,C-ND", 10, ""), ("D-ND", 1, "a,b\nc")])
    assert text == "2, A-ND, r1\n10, B C-ND\n1, D-ND, a b c\n"
    assert build_quickorder_text([]) == ""
    assert "unconfirmed" in build_quickorder_text.__doc__


# MCP tool

def call(tool, args):
    async def run():
        mcp = FastMCP("t")
        cart.register(mcp)
        async with Client(mcp) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(run())


ITEMS = [{"digikey_part_number": "A/B-ND", "quantity": 2, "customer_reference": "r1"}, {"digikey_part_number": "C-ND", "quantity": 1}]


def test_tool_urls_default():
    data = call("cart_links", {"items": ITEMS}).data
    assert data["output"] == "urls" and data["url_count"] == 1 and data["line_count"] == 2
    (u,) = data["urls"]
    assert u["url"].startswith("https://www.digikey.com/classic/ordering/fastadd.aspx?newcart=true&part1=A%2FB-ND")
    assert (u["first_line"], u["last_line"], u["length"]) == (1, 2, len(u["url"]))


def test_tool_text_and_html():
    assert call("cart_links", {"items": ITEMS, "output": "text"}).data["text"] == "2, A/B-ND, r1\n1, C-ND\n"
    data = call("cart_links", {"items": ITEMS, "output": "html"}).data
    assert data["form_count"] == 1 and "<form " in data["html"]


def test_tool_writes_html_only_to_given_path(tmp_path):
    target = tmp_path / "cart.html"
    data = call("cart_links", {"items": ITEMS, "output": "html", "write_html_to": str(target)}).data
    assert data["written_to"] == str(target) and "html" not in data
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert [p.name for p in tmp_path.iterdir()] == ["cart.html"]


@pytest.mark.parametrize(
    "args, message",
    [
        ({"items": ITEMS, "output": "urls", "write_html_to": "x.html"}, "only applies"),
        ({"items": ITEMS, "output": "html", "write_html_to": "notes.txt"}, ".html"),
        ({"items": ITEMS, "output": "html", "write_html_to": "/nonexistent-dir-xyz/a.html"}, "does not exist"),
        ({"items": ITEMS, "output": "csv"}, "output"),
        ({"items": [{"digikey_part_number": "", "quantity": 1}]}, "line 1: empty part number"),
        ({"items": [{"digikey_part_number": "A", "quantity": 0}]}, "line 1"),
    ],
)
def test_tool_errors_are_clean(args, message):
    with pytest.raises(ToolError) as info:
        call("cart_links", args)
    assert message in str(info.value)
    assert "Traceback" not in str(info.value)


def test_tool_makes_no_network_calls(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert cart.cart_links(ITEMS)["url_count"] == 1
    assert cart.cart_links(ITEMS, output="html")["form_count"] == 1
