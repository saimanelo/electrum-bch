#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python3 -*-
# This file (c) 2023 Calin Culianu <calin.culianu@gmail.com>
# Part of the Electron Cash SPV Wallet
# License: MIT
""" Encapsulation and handling of token metadata """

import json
import os
import threading

from abc import ABCMeta, abstractmethod
from typing import Any, Dict, Optional

from electroncash import util
from electroncash.simple_config import SimpleConfig


class TokenMeta(util.PrintError, metaclass=ABCMeta):

    def __init__(self, config: SimpleConfig):
        util.PrintError.__init__(self)
        self.config = config
        self.lock = threading.RLock()
        self.path = os.path.join(config.electrum_path(), "cashtoken_meta")
        self.make_dir(self.path)
        self.icons_path = os.path.join(self.path, "icons")
        self.make_dir(self.icons_path)
        self._icon_cache: Dict[str, Any] = dict()
        self.d: Dict[str, Any] = dict()
        self.dirty = False  # True if we wrote some keys to self.d, but they are not yet saved to disk
        self.load()

    def load(self):
        with self.lock:
            metafile = os.path.join(self.path, "metadata.json")
            if os.path.exists(metafile):
                try:
                    with open(metafile, "rt", encoding='utf-8') as f:
                        jdata = f.read()
                        self.d = json.loads(jdata)
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
                    self.print_error(f"Error loading {metafile}: {e!r}")

    def save(self, force=False):
        if not force and not self.dirty:
            return
        with self.lock:
            metafile = os.path.join(self.path, "metadata.json")
            metafile_tmp = metafile + ".tmp"
            try:
                jdata = json.dumps(self.d)
                with open(metafile_tmp, "wt", encoding='utf-8') as f:
                    f.write(jdata)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(metafile_tmp, metafile)
            except (TypeError, ValueError, json.JSONDecodeError, OSError) as e:
                self.print_error(f"Unable to save data to {metafile}: {e!r}")
            self.dirty = False

    @staticmethod
    def make_dir(path):
        util.make_dir(path)
        assert os.path.exists(path) and os.path.isdir(path)

    def get_icon(self, token_id_hex: str) -> Any:
        """ Gets the actual icon. On Qt for example this will return a QImage. Intended to be overridden """
        icon = self._icon_cache.get(token_id_hex)
        if icon:
            return icon
        buf = self._read_icon_file(self._icon_filepath(token_id_hex))
        if buf:
            icon = self._bytes_to_icon(buf)
        if not icon:
            icon = self._gen_default_icon(token_id_hex)
        assert icon is not None
        self._icon_cache[token_id_hex] = icon
        return icon

    def _icon_filepath(self, token_id_hex: str) -> str:
        return os.path.join(self.icons_path, token_id_hex) + "." + self._icon_ext

    def set_icon(self, token_id_hex: str, icon: Any):
        fname = self._icon_filepath(token_id_hex)
        buf = (icon is not None and self._icon_to_bytes(icon)) or None
        self._write_icon_file(fname, buf)
        if icon is not None:
            self._icon_cache[token_id_hex] = icon

    @property
    def _icon_ext(self) -> str:
        """Reimplement in subclasses to define the icon file extension. Default is "png" """
        return "png"

    def _read_icon_file(self, filepath: str) -> Optional[bytes]:
        with self.lock:
            if not os.path.exists(filepath):
                return None
            with open(filepath, "rb") as f:
                return f.read(1_000_000)  # Read up to 1MB

    @abstractmethod
    def _icon_to_bytes(self, icon: Any) -> bytes:
        """Reimplement in subclasses to take whatever icon format the platform expects and spit out bytes"""
        pass

    @abstractmethod
    def _bytes_to_icon(self, buf: bytes) -> Any:
        """Reimplement in subclasses to take whatever icon format the platform expects and spit out bytes"""
        pass

    @abstractmethod
    def _gen_default_icon(self, token_id_hex: str) -> Any:
        """Reimplement in subclasses to generate a default icon for a token_id if the icon file is missing"""
        pass

    def _write_icon_file(self, filepath: str, buf: Optional[bytes]):
        with self.lock:
            try:
                os.remove(filepath)
            except OSError:
                pass
            if buf is None:
                return
            with open(filepath, "wb") as f:
                f.write(buf)


    def get_token_display_name(self, token_id_hex: str) -> str:
        """Returns the token_id_hex if not found, otherwise returns the display name"""
        return str(self.d.get("display_names", {}).get(token_id_hex) or token_id_hex)

    def get_token_ticker_symbol(self, token_id_hex: str) -> Optional[str]:
        return self.d.get("tickers", {}).get(token_id_hex)

    def get_token_decimals(self, token_id_hex: str) -> int:
        """Returns 0 if unknown or undefined decimals for token"""
        return int(self.d.get("decimals", {}).get(token_id_hex, 0))

    def set_token_display_name(self, token_id_hex: str, name: str):
        dd = self.d.get("display_names", {})
        was_empty = not dd
        dd[token_id_hex] = str(name)
        if was_empty:
            self.d["display_names"] = dd
        self.dirty = True

    def set_token_ticker_symbol(self, token_id_hex: str, ticker: str):
        dd = self.d.get("tickers", {})
        was_empty = not dd
        dd[token_id_hex] = str(ticker)
        if was_empty:
            self.d["tickers"] = dd
        self.dirty = True

    def set_token_decimals(self, token_id_hex: str, decimals: int):
        dd = self.d.get("decimals", {})
        was_empty = not dd
        dd[token_id_hex] = int(decimals)
        if was_empty:
            self.d["tickers"] = dd
        self.dirty = True
