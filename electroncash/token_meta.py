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
from typing import Any, Dict, Optional, Union

from electroncash import token, util
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
            icon = self.gen_default_icon(token_id_hex)
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
    def gen_default_icon(self, token_id_hex: str) -> Any:
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

    def get_token_display_name(self, token_id_hex: str) -> Optional[str]:
        """Returns None if not found or if empty, otherwise returns the display name if found and not empty"""
        ret = self.d.get("display_names", {}).get(token_id_hex)
        if isinstance(ret, str):
            return ret

    def get_token_ticker_symbol(self, token_id_hex: str) -> Optional[str]:
        ret = self.d.get("tickers", {}).get(token_id_hex)
        if isinstance(ret, str):
            return ret

    def get_token_decimals(self, token_id_hex: str) -> Optional[int]:
        """Returns None if unknown or undefined decimals for token"""
        ret = self.d.get("decimals", {}).get(token_id_hex)
        if isinstance(ret, int):
            return ret
        return ret

    def set_token_display_name(self, token_id_hex: str, name: Optional[str]):
        dd = self.d.get("display_names", {})
        if name is None:
            dd.pop(token_id_hex, None)
        elif isinstance(name, str):
            was_empty = not dd
            dd[token_id_hex] = str(name)
            if was_empty:
                self.d["display_names"] = dd
        self.dirty = True

    def set_token_ticker_symbol(self, token_id_hex: str, ticker: Optional[str]):
        dd = self.d.get("tickers", {})
        if ticker is None:
            dd.pop(token_id_hex, None)
        elif isinstance(ticker, str):
            was_empty = not dd
            dd[token_id_hex] = str(ticker)
            if was_empty:
                self.d["tickers"] = dd
        self.dirty = True

    def set_token_decimals(self, token_id_hex: str, decimals: Optional[int]):
        dd = self.d.get("decimals", {})
        if decimals is None:
            dd.pop(token_id_hex, None)
        elif isinstance(decimals, int):
            was_empty = not dd
            dd[token_id_hex] = int(decimals)
            if was_empty:
                self.d["decimals"] = dd
        self.dirty = True

    @staticmethod
    def _normalize_to_token_id_hex(token_or_id: Union[str, token.OutputData, bytes]) -> str:
        assert isinstance(token_or_id, (str, bytes, bytearray, token.OutputData))
        if isinstance(token_or_id, str):
            return token_or_id
        elif isinstance(token_or_id (bytes, bytearray)):
            return token_or_id.hex()
        else:
            return token_or_id.id_hex

    def format_amount(self, token_or_id: Union[str, token.OutputData, bytes], fungible_amount: int,
                      num_zeros=0, is_diff=False, whitespace=False, precision=None,
                      append_tokentoshis=False) -> str:
        """Formats a particular token's amount string, according to that token's metadata spec for decimals.
        If the token is unknown we tread the 'decimals' for that token as '0'."""
        token_id_hex = self._normalize_to_token_id_hex(token_or_id)
        decimals = self.get_token_decimals(token_id_hex)
        if not isinstance(decimals, int):
            decimals = 0
        return token.format_fungible_amount(fungible_amount, decimal_point=decimals, num_zeros=num_zeros,
                                            precision=precision, is_diff=is_diff, whitespaces=whitespace,
                                            append_tokentoshis=append_tokentoshis)

    def parse_amount(self, token_or_id: Union[str, token.OutputData, bytes], val: str) -> int:
        """Inverse of above"""
        token_id_hex = self._normalize_to_token_id_hex(token_or_id)
        decimals = self.get_token_decimals(token_id_hex)
        if not isinstance(decimals, int):
            decimals = 0
        return token.parse_fungible_amount(val, decimal_point=decimals)

    def format_token_display_name(self, token_or_id: Union[str, token.OutputData, bytes],
                                  format_str="{token_name} ({token_symbol})"):
        token_id_hex = self._normalize_to_token_id_hex(token_or_id)
        tn = self.get_token_display_name(token_id_hex)
        if tn:
            tn = tn.strip()
        tn = tn or token_id_hex
        tsym = self.get_token_ticker_symbol(token_id_hex)
        if tsym:
            tsym = tsym.strip()
        if not tsym:
            return tn
        return format_str.format(token_name=tn, token_symbol=tsym)
