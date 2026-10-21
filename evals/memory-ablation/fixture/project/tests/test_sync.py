from pathlib import Path

from tidepool import cli


def test_render_lists_every_item():
    text = cli._render([{"title": "A", "url": "a"}, {"title": "B", "url": "b"}])
    assert text.count("- [") == 2


def test_status_without_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "STATE", tmp_path / "none.json")
    assert cli.status() == 1
    assert "no run" in capsys.readouterr().out
