"""Setup configuration for claude-chat-extractor package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme = Path("README.md").read_text(encoding="utf-8")

setup(
    name="claude-chat-extractor",
    version="1.0.0",
    author="Your Name",
    description="Extract and consolidate shared Claude conversations",
    long_description=readme,
    long_description_content_type="text/markdown",
    url="https://github.com/dzivkovi/claude-chat-extractor",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        "playwright>=1.40.0",
    ],
    entry_points={
        "console_scripts": [
            "claude-chat-extractor=claude_chat_extractor.extractor:main",
        ],
    },
)
