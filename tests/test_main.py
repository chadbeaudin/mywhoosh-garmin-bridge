import logging

import fit_tool.fit_file  # noqa: F401  (configures the root logger on import)

from bridge.__main__ import configure_logging


def test_logging_reaches_info_despite_fit_tool_configuring_root(monkeypatch, capsys):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging()
    logging.getLogger("bridge.app").info("Bridge started")
    assert "INFO bridge.app: Bridge started" in capsys.readouterr().err
