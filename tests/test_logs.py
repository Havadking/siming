import asyncio

from devpanel import logs as L
from devpanel.logs import ProjectLog, clean, decode


def test_decode_fallbacks():
    assert decode("中文".encode("utf-8")) == "中文"
    assert decode("中文".encode("gbk")) == "中文"
    assert "�" in decode(b"\xff\xfe\xfd")


def test_clean_strips_ansi_and_cr():
    assert clean("\x1b[32mok\x1b[0m\r\n") == "ok"
    assert clean("\x1b]0;title\x07plain") == "plain"


def test_file_write_and_preload(tmp_path):
    path = tmp_path / "x.log"
    log = ProjectLog(path)
    for i in range(300):
        log.append(f"line {i}")
    log.close()
    assert path.read_text(encoding="utf-8").splitlines()[-1] == "line 299"
    # 重新打开：从文件尾部捞回最近 200 行
    log2 = ProjectLog(path)
    assert log2.tail(5) == [f"line {i}" for i in range(295, 300)]
    assert len(log2.buffer) == L.REPLAY_LINES


def test_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "MAX_BYTES", 200)
    path = tmp_path / "r.log"
    log = ProjectLog(path)
    for i in range(100):
        log.append("x" * 20)
    log.close()
    assert path.exists()
    assert (tmp_path / "r.log.1").exists()
    assert not (tmp_path / "r.log.4").exists()


def test_subscribe_receives_lines(tmp_path):
    log = ProjectLog(tmp_path / "s.log")
    log.append("before")

    async def go():
        replay, q = log.subscribe()
        assert replay == ["before"]
        loop = asyncio.get_running_loop()
        # 模拟读管道的线程从别的线程写入
        await loop.run_in_executor(None, log.append, "after")
        got = await asyncio.wait_for(q.get(), 2)
        log.unsubscribe(q)
        return got

    assert asyncio.run(go()) == "after"
