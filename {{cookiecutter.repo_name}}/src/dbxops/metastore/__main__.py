"""Entry point for ``python -m dbxops.metastore``."""

import sys

from dbxops.metastore.cli import main

if __name__ == "__main__":
    sys.exit(main())
