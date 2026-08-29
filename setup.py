"""Compatibility metadata for older setuptools versions.

Modern installers read the canonical project metadata from pyproject.toml. This
small shim keeps local, offline builds working with the setuptools bundled by
older Python installations as well.
"""

from setuptools import find_packages, setup


setup(
    name="buildbrake",
    version="0.1.0",
    description="A local outcome contract and runtime guard for coding-agent commands",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={"buildbrake": ["static/*.html"]},
    python_requires=">=3.9",
    entry_points={"console_scripts": ["buildbrake=buildbrake.cli:main"]},
)
