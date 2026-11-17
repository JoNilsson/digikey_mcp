"""doctor: offline credential report, secrets never printed, --online with a mocked token call."""

from __future__ import annotations

import json

import httpx
import pytest

from digikey_mcp.cli import common
from digikey_mcp.client import DigiKeyClient

SECRET_ID = "id-value-SHOULD-NOT-PRINT"
SECRET_VALUE = "secret-value-SHOULD-NOT-PRINT"
VARS = ("CLIENT_ID", "CLIENT_SECRET", "CLIENT_ID_FILE", "CLIENT_SECRET_FILE", "DIGIKEY_SANDBOX")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def files(tmp_path, monkeypatch):
    cid, csec = tmp_path / "client_id", tmp_path / "client_secret"
    cid.write_text(SECRET_ID + "\n", encoding="utf-8")
    csec.write_text(SECRET_VALUE, encoding="utf-8")
    monkeypatch.setenv("CLIENT_ID_FILE", str(cid))
    monkeypatch.setenv("CLIENT_SECRET_FILE", str(csec))
    return cid, csec


def _token_client(status: int):
    def handler(request):
        assert request.url.path == "/v1/oauth2/token"
        if status == 200:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 600})
        return httpx.Response(status, json={"error_description": "invalid client"})

    return lambda: DigiKeyClient("id", "secret", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_nothing_set_reports_both_missing_and_exits_1(run):
    code, out, _ = run("doctor")
    assert code == 1
    assert "CLIENT_ID" in out and "neither CLIENT_ID_FILE nor CLIENT_ID is set" in out
    assert "neither CLIENT_SECRET_FILE nor CLIENT_SECRET is set" in out
    assert "production" in out and "not run" in out
    assert "README.md" in out


def test_usable_files_exit_0_and_print_no_values(run, files):
    code, out, err = run("doctor")
    assert code == 0
    assert str(files[0]) in out and str(files[1]) in out
    assert SECRET_ID not in out + err and SECRET_VALUE not in out + err


def test_variables_are_reported_by_name_only(run, monkeypatch):
    monkeypatch.setenv("CLIENT_ID", SECRET_ID)
    monkeypatch.setenv("CLIENT_SECRET", SECRET_VALUE)
    code, out, err = run("doctor")
    assert code == 0
    assert "from variable CLIENT_ID" in out
    assert SECRET_ID not in out + err and SECRET_VALUE not in out + err


def test_missing_and_empty_files_are_named(run, files, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_ID_FILE", "/home/you/.config/digikey/client_id")
    files[1].write_text("  \n", encoding="utf-8")
    code, out, _ = run("doctor")
    assert code == 1
    assert "file not found: /home/you/.config/digikey/client_id" in out
    assert f"file empty: {files[1]}" in out


def test_sandbox_mode_is_reported(run, files, monkeypatch):
    monkeypatch.setenv("DIGIKEY_SANDBOX", "true")
    code, out, _ = run("doctor")
    assert code == 0
    assert "sandbox (https://sandbox-api.digikey.com)" in out


def test_json_output_has_no_values(run, files):
    code, out, _ = run("doctor", "--json")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True and data["mode"] == "production"
    assert [c["name"] for c in data["credentials"]] == ["CLIENT_ID", "CLIENT_SECRET"]
    assert SECRET_ID not in out and SECRET_VALUE not in out


def test_offline_doctor_makes_no_token_request(run, files, monkeypatch):
    def boom():
        raise AssertionError("doctor without --online must not build a client")

    monkeypatch.setattr(common, "get_client", boom)
    assert run("doctor")[0] == 0


def test_online_success(run, files, monkeypatch):
    monkeypatch.setattr(common, "get_client", _token_client(200))
    code, out, _ = run("doctor", "--online")
    assert code == 0
    assert "token request   ok" in out


def test_online_rejection_exits_1_with_reason(run, files, monkeypatch):
    monkeypatch.setattr(common, "get_client", _token_client(401))
    code, out, _ = run("doctor", "--online")
    assert code == 1
    assert "failed" in out and "401" in out and "invalid client" in out


def test_online_skipped_without_credentials(run, monkeypatch):
    def boom():
        raise AssertionError("no client without credentials")

    monkeypatch.setattr(common, "get_client", boom)
    code, out, _ = run("doctor", "--online")
    assert code == 1
    assert "skipped: credentials incomplete" in out
