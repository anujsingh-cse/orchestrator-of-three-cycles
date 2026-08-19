"""Curation policy for failure zoo licensing (T9, D19)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.zoo.view import ZooSpecimen


PERMISSIVE_LICENSES = {
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "ISC",
    "Unlicense",
    "Zlib",
    "Python-2.0",
}


NON_PERMISSIVE_LICENSES = {
    "GPL-2.0",
    "GPL-3.0",
    "AGPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "MPL-2.0",
    "EPL-2.0",
    "CDDL-1.0",
}


@dataclass(frozen=True)
class CurationPolicy:
    """Policy for including/distilling specimens in the zoo."""

    # Licenses that allow verbatim distribution
    permissive_licenses: frozenset[str] = frozenset(PERMISSIVE_LICENSES)
    # Licenses that require distillation
    non_permissive_licenses: frozenset[str] = frozenset(NON_PERMISSIVE_LICENSES)

    # Maximum verbatim characters from non-permissive sources
    max_verbatim_chars: int = 0  # Zero tolerance for non-permissive

    # Whether to include specimens with unknown licenses
    allow_unknown_license: bool = False

    def should_include(self, specimen: ZooSpecimen) -> bool:
        """Whether to include this specimen in the zoo."""
        if not specimen.license:
            return self.allow_unknown_license

        # Normalize license
        license_norm = specimen.license.strip().upper()

        # Permissive licenses - always include
        if license_norm in self.permissive_licenses:
            return True

        # Non-permissive - include only if distilled
        if license_norm in self.non_permissive_licenses:
            return True  # Will be distilled

        # Unknown license - conservative: include but distill
        return self.allow_unknown_license

    def should_distill(self, specimen: ZooSpecimen) -> bool:
        """Whether to distill (remove verbatim code) this specimen."""
        if not specimen.license:
            return not self.allow_unknown_license

        license_norm = specimen.license.strip().upper()

        # Non-permissive licenses must be distilled
        if license_norm in self.non_permissive_licenses:
            return True

        # Permissive licenses - never distill
        if license_norm in self.permissive_licenses:
            return False

        # Unknown license - conservative: distill if verbatim content is large
        verbatim_chars = len(specimen.patch) + len(specimen.attack) + len(specimen.critique)
        return verbatim_chars > self.max_verbatim_chars


class LicenseClassifier:
    """Classify repository licenses from SPDX identifiers or license files."""

    # Common license file patterns
    LICENSE_FILES = [
        "LICENSE", "LICENSE.txt", "LICENSE.md",
        "COPYING", "COPYING.txt", "COPYING.md",
        "LICENSE-APACHE", "LICENSE-MIT",
    ]

    SPDX_MAP = {
        "mit": "MIT",
        "bsd-2-clause": "BSD-2-Clause",
        "bsd-3-clause": "BSD-3-Clause",
        "apache-2.0": "Apache-2.0",
        "apache 2.0": "Apache-2.0",
        "gpl-2.0": "GPL-2.0",
        "gpl-3.0": "GPL-3.0",
        "agpl-3.0": "AGPL-3.0",
        "lgpl-2.1": "LGPL-2.1",
        "lgpl-3.0": "LGPL-3.0",
        "mpl-2.0": "MPL-2.0",
        "epl-2.0": "EPL-2.0",
        "isc": "ISC",
        "unlicense": "Unlicense",
        "zlib": "Zlib",
        "python-2.0": "Python-2.0",
    }

    @classmethod
    def from_spdx(cls, spdx: str) -> str | None:
        """Normalize SPDX identifier."""
        if not spdx:
            return None
        normalized = spdx.strip().lower()
        return cls.SPDX_MAP.get(normalized, spdx.strip())

    @classmethod
    def from_pyproject(cls, pyproject_path: Path) -> str | None:
        """Extract license from pyproject.toml."""
        import tomllib
        try:
            with pyproject_path.open("rb") as f:
                data = tomllib.load(f)
            # Check project.license
            license_data = data.get("project", {}).get("license")
            if isinstance(license_data, dict):
                return cls.from_spdx(license_data.get("text", "") or license_data.get("file", ""))
            elif isinstance(license_data, str):
                return cls.from_spdx(license_data)
            # Check tool.poetry.license
            license_data = data.get("tool", {}).get("poetry", {}).get("license")
            if license_data:
                return cls.from_spdx(str(license_data))
        except Exception:
            pass
        return None

    @classmethod
    def from_setup_cfg(cls, setup_cfg_path: Path) -> str | None:
        """Extract license from setup.cfg."""
        import configparser
        try:
            config = configparser.ConfigParser()
            config.read(setup_cfg_path)
            license_str = config.get("metadata", "license", fallback="")
            return cls.from_spdx(license_str) if license_str else None
        except Exception:
            return None

    @classmethod
    def from_license_file(cls, repo_path: Path) -> str | None:
        """Try to classify license from license file content."""
        for name in cls.LICENSE_FILES:
            path = repo_path / name
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="ignore").lower()
                # Simple keyword matching
                if "mit license" in content or "permission is hereby granted" in content:
                    return "MIT"
                if "apache license" in content and "version 2.0" in content:
                    return "Apache-2.0"
                if "gnu general public license" in content:
                    if "version 3" in content:
                        return "GPL-3.0"
                    return "GPL-2.0"
                if "gnu affero general public license" in content:
                    return "AGPL-3.0"
                if "gnu lesser general public license" in content:
                    if "version 3" in content:
                        return "LGPL-3.0"
                    return "LGPL-2.1"
                if "mozilla public license" in content:
                    return "MPL-2.0"
                if "eclipse public license" in content:
                    return "EPL-2.0"
                if "bsd" in content and "3-clause" in content:
                    return "BSD-3-Clause"
                if "bsd" in content and "2-clause" in content:
                    return "BSD-2-Clause"
        return None

    @classmethod
    def detect(cls, repo_path: Path) -> str | None:
        """Detect license from repository."""
        # Try pyproject.toml
        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            result = cls.from_pyproject(pyproject)
            if result:
                return result

        # Try setup.cfg
        setup_cfg = repo_path / "setup.cfg"
        if setup_cfg.exists():
            result = cls.from_setup_cfg(setup_cfg)
            if result:
                return result

        # Try license files
        return cls.from_license_file(repo_path)

    @classmethod
    def is_permissive(cls, spdx: str | None) -> bool:
        """Check if license is permissive."""
        if not spdx:
            return False
        normalized = cls.from_spdx(spdx) or spdx.strip().upper()
        return normalized in PERMISSIVE_LICENSES
