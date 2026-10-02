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


def _setup_tls():
    """Python's OpenSSL doesn't use the macOS Keychain; it looks for a CA file at a path baked
    in at build time, which exists on the build machine but not on users' Macs, so every
    HTTPS request through urllib failed with CERTIFICATE_VERIFY_FAILED. Use the CA bundle we
    ship (certifi, Mozilla's list) unless the user configured their own."""
    if sys.platform != "darwin" or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi

        if os.path.exists(certifi.where()):
            os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass


def main():
    _setup_tls()
    _setup_logging()
    if "--update-now" in sys.argv:  # check + download + hand over to the installer (tests/CI)
        from .update import run_cli

        i = sys.argv.index("--update-now")
        return run_cli(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
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
