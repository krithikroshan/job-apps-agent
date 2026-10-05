"""CLI commands that don't need a database."""

from jobs_agent import cli, crypto


def test_gen_key_prints_a_usable_env_line(capsys, monkeypatch):
    cli.main(["gen-key"])
    line = capsys.readouterr().out.strip()
    name, _, value = line.partition("=")
    assert name == crypto.ENV_VAR
    monkeypatch.setenv(crypto.ENV_VAR, value)
    assert crypto.decrypt(crypto.encrypt("x")) == "x"
