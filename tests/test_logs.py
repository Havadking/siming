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
    for i in range(20):
        log.append("x" * 20)
    log.rotate_if_needed()                    # 项目启动前滚一次
    assert (tmp_path / "r.log.1").exists() and not path.exists()
    log.append("fresh")
    assert path.exists() and log.tail(1) == ["fresh"]
    for _ in range(3):
        for i in range(20):
            log.append("y" * 20)
        log.rotate_if_needed()
    assert (tmp_path / "r.log.3").exists()
    assert not (tmp_path / "r.log.4").exists()  # 只留 3 份
    log.close()


def test_child_handle_appends_and_tail_picks_up(tmp_path):
    """子进程用的句柄和面板自己的句柄同时写，谁都不覆盖谁；tail 线程能捞到。"""
    import subprocess, sys, time
    path = tmp_path / "c.log"
    log = ProjectLog(path)
    log.append("panel-1")
    out = log.open_for_child()
    p = subprocess.Popen([sys.executable, "-c", "print('child-1', flush=True); print('child-2', flush=True)"],
                         stdout=out, stderr=subprocess.STDOUT)
    out.close()
    p.wait(timeout=10)
    log.append("panel-2")
    time.sleep(0.5)
    assert log.tail(10) == ["panel-1", "child-1", "child-2", "panel-2"]
    assert path.read_text(encoding="utf-8").splitlines() == ["panel-1", "child-1", "child-2", "panel-2"]
    log.close()


def test_rotate_while_child_holds_handle(tmp_path, monkeypatch):
    """Windows 上子进程握着句柄时也能改名（FILE_SHARE_DELETE）。"""
    monkeypatch.setattr(L, "MAX_BYTES", 10)
    path = tmp_path / "h.log"
    log = ProjectLog(path)
    log.append("0123456789ABCDEF")
    holder = log.open_for_child()
    try:
        log.rotate_if_needed()
        assert (tmp_path / "h.log.1").exists()
    finally:
        holder.close()
        log.close()


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
