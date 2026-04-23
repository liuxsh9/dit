from pathlib import Path
from typing import Optional


class RefStore:
    def __init__(self, dot_datahub: Path):
        self.dot = dot_datahub
        self.head_file = dot_datahub / "HEAD"
        self.refs_dir = dot_datahub / "refs" / "heads"
        self.tags_dir = dot_datahub / "refs" / "tags"

    def init(self) -> None:
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        self.tags_dir.mkdir(parents=True, exist_ok=True)
        if not self.head_file.exists():
            self.head_file.write_text("ref:main\n")

    def get_head(self) -> str:
        return self.head_file.read_text().strip()

    def current_branch(self) -> Optional[str]:
        head = self.get_head()
        if head.startswith("ref:"):
            return head[4:]
        return None

    def resolve_head(self) -> Optional[str]:
        head = self.get_head()
        if head.startswith("ref:"):
            return self.get_branch(head[4:])
        return head

    def get_branch(self, name: str) -> Optional[str]:
        path = self.refs_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def set_branch(self, name: str, commit_hash: str) -> None:
        path = self.refs_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit_hash + "\n")

    def list_branches(self) -> dict[str, str]:
        result = {}
        if self.refs_dir.exists():
            for f in self.refs_dir.iterdir():
                if f.is_file():
                    result[f.name] = f.read_text().strip()
        return result

    def delete_branch(self, name: str) -> bool:
        path = self.refs_dir / name
        if not path.exists():
            return False
        path.unlink()
        return True

    def get_tag(self, name: str) -> str | None:
        path = self.tags_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def set_tag(self, name: str, commit_hash: str) -> None:
        self.tags_dir.mkdir(parents=True, exist_ok=True)
        path = self.tags_dir / name
        path.write_text(commit_hash + "\n")

    def delete_tag(self, name: str) -> bool:
        path = self.tags_dir / name
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_tags(self) -> dict[str, str]:
        result = {}
        if self.tags_dir.exists():
            for f in self.tags_dir.iterdir():
                if f.is_file():
                    result[f.name] = f.read_text().strip()
        return result
