import contextlib
import gzip as gzip_lib
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch
from gzip import GzipFile
from io import TextIOWrapper

from loguru import logger


@dataclass
class InputDataFile:
    path: str

    @contextmanager
    def open_binary(self):
        with open(self.path, mode="rb") as f:
            yield f

    @contextmanager
    def open(self, gzip: bool = False, binary=False):
        with self.open_binary() as fo:
            if gzip:
                with GzipFile(mode="r" if not binary else "rb", fileobj=fo) as gf:
                    if binary:
                        yield gf
                    else:
                        with TextIOWrapper(gf) as f:
                            yield f
            else:
                if binary:
                    yield fo
                else:
                    with TextIOWrapper(fo) as f:
                        yield f


@dataclass
class BaseInputDataFolder(ABC):
    path: str
    extension: str | list[str] = None
    recursive: bool = True
    match_pattern: str = None

    @abstractmethod
    def list_files(self, extension: str | list[str] = None) -> list[InputDataFile]:
        logger.error(
            "Do not instantiate BaseInputDataFolder directly, " "use a LocalInputDataFolder or S3InputDataFolder"
        )
        raise NotImplementedError

    def __post_init__(self):
        self._lock = contextlib.nullcontext()

    def set_lock(self, lock):
        self._lock = lock

    def get_files_shard(self, rank: int, world_size: int) -> list[InputDataFile]:
        return self.list_files()[rank::world_size]

    def _match_file(self, file_path, extension=None):
        extensions = (
            ([self.extension] if type(self.extension) == str else self.extension)
            if not extension
            else ([extension] if type(extension) == str else extension)
        )
        return (not extensions or get_extension(file_path) in extensions) and (  # check extension  # check pattern
            not self.match_pattern or fnmatch(os.path.relpath(file_path, self.path), self.match_pattern)
        )


def get_extension(filepath):
    exts = []
    stem, ext = os.path.splitext(filepath)
    while ext:
        exts.append(ext)
        stem, ext = os.path.splitext(stem)
    return "".join(reversed(exts))


@dataclass
class OutputDataFile(ABC):
    local_path: str
    path: str
    relative_path: str
    file_handler = None
    nr_documents: int = 0

    def close(self):
        if self.file_handler:
            self.file_handler.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self, mode: str = "w", gzip: bool = False, overwrite: bool = False):
        if not self.file_handler or overwrite:
            os.makedirs(os.path.dirname(self.local_path), exist_ok=True)
            self.file_handler = open(self.local_path, mode) if not gzip else gzip_lib.open(self.local_path, mode)
        return self


@dataclass
class BaseOutputDataFolder(ABC):
    path: str
    local_path: str
    _output_files: dict[str, OutputDataFile] = field(default_factory=dict)

    def close(self):
        for file in self._output_files.values():
            file.close()

    @abstractmethod
    def create_new_file(self, relative_path: str) -> OutputDataFile:
        logger.error(
            "Do not instantiate a BaseOutputDataFolder directly, " "use a LocalOutputDataFolder or S3OutputDataFolder"
        )
        raise NotImplementedError

    def __post_init__(self):
        self._lock = contextlib.nullcontext()

    def set_lock(self, lock):
        self._lock = lock

    def delete_file(self, relative_path: str):
        if relative_path in self._output_files:
            output_file = self._output_files.pop(relative_path)
            output_file.close()
            if output_file.local_path and os.path.isfile(output_file.local_path):
                os.remove(output_file.local_path)

    def open(self, relative_path: str, mode: str = "w", gzip: bool = False, overwrite: bool = False):
        if relative_path not in self._output_files or overwrite:
            new_output_file = self.create_new_file(relative_path)
            new_output_file.open(mode, gzip, overwrite=overwrite)
            self._output_files[relative_path] = new_output_file
        return self._output_files[relative_path]
