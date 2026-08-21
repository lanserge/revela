# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""``python -m revela``: the same command as the ``revela`` script, for
environments that have the package on the path but not its entry point."""
import sys

from revela.cli import main

sys.exit(main())
