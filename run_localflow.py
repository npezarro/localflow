import multiprocessing
import os
import sys

if __name__ == "__main__":
    # Frozen apps must handle multiprocessing's helper re-exec (resource tracker) here,
    # otherwise the child relaunches the whole app and the parent hangs on exit.
    multiprocessing.freeze_support()
    from localflow.__main__ import main

    code = main()
    if "--selftest" in sys.argv:
        sys.stdout and sys.stdout.flush()
        os._exit(code)
    sys.exit(code)
