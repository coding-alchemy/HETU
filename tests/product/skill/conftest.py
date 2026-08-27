"""Keep the canonical Skill scripts directory free of bytecode caches.

Tests in this package load ``skills/hetu-stock-analysis/scripts/*.py`` in
process (via ``deterministic_tool_loader``) and through real subprocess CLI
calls. Both paths would otherwise drop ``__pycache__/*.pyc`` files inside the
Skill package; the manifest and package validators treat every file under the
skill root as an installable resource, so cached bytecode must never be
written there. Setting ``sys.dont_write_bytecode`` covers in-process loading
and exporting ``PYTHONDONTWRITEBYTECODE`` covers the subprocess CLI calls.
"""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
