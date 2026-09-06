"""Compatibility metadata for older setuptools versions.

Modern installers read the canonical project metadata from pyproject.toml. This
small shim keeps local, offline builds working with the setuptools bundled by
older Python installations as well.
"""

from pathlib import Path

from setuptools import find_packages, setup


setup(
    name="buildbrake",
    version="0.1.0",
    description="A local outcome contract and runtime guard for coding-agent commands",
    long_description=(Path(__file__).parent / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={"buildbrake": ["static/*.html"]},
    python_requires=">=3.9",
    license="MIT",
    entry_points={
        "console_scripts": [
            "bb=buildbrake.cli:main",
            "buildbrake=buildbrake.cli:main",
        ]
    },
)
