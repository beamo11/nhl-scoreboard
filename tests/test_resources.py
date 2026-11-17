import os

from scoreboard.web.resources import ResourceMonitor


def test_resource_monitor_calculates_host_cpu_and_memory(tmp_path):
    proc = tmp_path
    (proc / "self/task/1").mkdir(parents=True)
    (proc / "self/task/2").mkdir()
    (proc / "self/statm").write_text("100 5 0 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
    (proc / "stat").write_text("cpu 100 0 50 850 0 0 0 0\n")
    times = iter((10.0, 15.0))
    monitor = ResourceMonitor(proc_root=proc, clock=lambda: next(times))

    first = monitor.sample()
    (proc / "stat").write_text("cpu 200 0 150 1650 0 0 0 0\n")
    second = monitor.sample()

    assert first["host"]["cpu_percent"] is None
    assert second["host"]["cpu_percent"] == 20.0
    assert second["host"]["memory_used_bytes"] == 750 * 1024
    assert second["host"]["memory_total_bytes"] == 1000 * 1024
    assert second["process"]["rss_bytes"] == 5 * os.sysconf("SC_PAGE_SIZE")
    assert second["process"]["threads"] == 2