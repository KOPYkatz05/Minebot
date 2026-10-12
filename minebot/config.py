"""Validated collection defaults; credentials never belong in configuration."""

from dataclasses import asdict, dataclass, field
import re


@dataclass
class Config:
    queries: list[str] = field(default_factory=lambda: [
        "litematic in:name,description,readme",
        "litematica schematics in:readme",
        "minecraft schematics in:name,description,readme",
        "topic:litematica",
    ])
    watched_repositories: list[str] = field(default_factory=list)
    excluded_repositories: list[str] = field(default_factory=list)
    max_requests: int = 500
    max_seconds: int = 7200
    max_new_files: int = 100
    max_download_bytes: int = 1024**3
    max_storage_bytes: int = 10 * 1024**3
    paused: bool = False

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict) or set(values) - set(cls.__dataclass_fields__):
            raise ValueError("Configuration contains unknown fields; credentials are not accepted")
        result = cls(**values)
        for name in ("max_requests", "max_seconds", "max_new_files", "max_download_bytes", "max_storage_bytes"):
            value = getattr(result, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(result.paused) is not bool:
            raise ValueError("paused must be boolean")
        if not isinstance(result.queries, list) or len(result.queries) > 100:
            raise ValueError("queries must be a list of at most 100 queries")
        if any(not isinstance(q, str) or not q.strip() or len(q) > 256 for q in result.queries):
            raise ValueError("Queries must contain 1–256 characters")
        for name in ("watched_repositories", "excluded_repositories"):
            items = getattr(result, name)
            if not isinstance(items, list) or any(not isinstance(r, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) for r in items):
                raise ValueError(f"{name} entries must be owner/repository names")
        return result

    def document(self):
        return asdict(self)
