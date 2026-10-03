"""Prompts are files, and each one's version is a hash of its text.

A prompt is code that a model runs. When an alert's explanation looks wrong a month later, the
first question is which prompt wrote it. A hand-kept version number is forgotten at the first
quick edit, so the version is the text itself, hashed, and it is saved with every answer.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

FOLDER = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str

    @property
    def version(self) -> str:
        return version_of(self.text)


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


@cache
def load(name: str) -> Prompt:
    return Prompt(name, (FOLDER / f"{name}.md").read_text(encoding="utf-8"))


def versions() -> dict[str, str]:
    return {path.stem: load(path.stem).version for path in sorted(FOLDER.glob("*.md"))}
