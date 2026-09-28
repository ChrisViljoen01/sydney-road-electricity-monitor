"""Windows desktop entry point used by the packaged application."""

from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    from electricity_tool.desktop import run_desktop

    run_desktop()
