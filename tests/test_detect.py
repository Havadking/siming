import json
from pathlib import Path

from devpanel.detect import detect, slug


def test_slug():
    assert slug("My Project_2") == "my-project-2"
    assert slug("拾光笺") == "project"
    assert slug("@scope/pkg") == "scope-pkg"


def test_node_dev_with_vite_port(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "@me/blog", "scripts": {"dev": "vite", "build": "vite build", "start": "node s.js"}}))
    (tmp_path / "vite.config.ts").write_text("export default { server: { port: 5173 } }")
    (tmp_path / "node_modules").mkdir()
    d = detect(tmp_path)
    assert d["kind"] == "node" and d["name"] == "blog" and d["id"] == "blog"
    assert d["cmd"] == "npm run dev"
    assert d["candidates"] == ["npm run dev", "npm start"]
    assert d["port"] == 5173
    assert d["notes"] == []


def test_node_pnpm_and_port_in_script(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev -p 3001"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    d = detect(tmp_path)
    assert d["cmd"] == "pnpm dev" and d["port"] == 3001
    assert "node_modules" in d["notes"][0]


def test_python_pyproject_scripts(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "vsum"\n[project.scripts]\nvsum = "vsum.cli:main"\n')
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".env").write_text("PORT=7860\n")
    d = detect(tmp_path)
    assert d["kind"] == "python" and d["name"] == "vsum" and d["cmd"] == "uv run --no-sync vsum" and d["port"] == 7860


def test_python_plain_entry(tmp_path: Path):
    (tmp_path / "server.py").write_text("DEFAULT_PORT = 17777\n")
    d = detect(tmp_path)
    assert d["cmd"] == "python server.py" and d["port"] == 17777


def test_unknown_dir(tmp_path: Path):
    d = detect(tmp_path)
    assert d["kind"] == "unknown" and d["cmd"] is None and d["notes"]


def test_missing_dir(tmp_path: Path):
    d = detect(tmp_path / "nope")
    assert d["notes"] == ["目录不存在"]
