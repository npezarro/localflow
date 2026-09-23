import logging
import logging.handlers
import os
import sys


def _setup_logging():
    from . import paths

    handler = logging.handlers.RotatingFileHandler(
        os.path.join(paths.data_dir(), "localflow.log"), maxBytes=1_000_000, backupCount=2,
        encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main():
    _setup_logging()
    if "--selftest" in sys.argv:
        from .selftest import run

        return run(sys.argv[1:])
    from .app import main as app_main

    try:
        return app_main()
    except Exception:
        logging.getLogger("localflow").exception("fatal")
        raise


if __name__ == "__main__":
    sys.exit(main())
