#!/usr/bin/env python3
#
# Electron Cash - lightweight Bitcoin Cash client
# Copyright (C) 2022 The Electron Cash Developers
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from collections import defaultdict
from enum import IntEnum
from functools import wraps
from typing import DefaultDict, Dict, List, Optional, Set

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QAbstractItemView, QMenu
from PyQt5.QtGui import QFont

from electroncash.i18n import _, ngettext
from electroncash import token, util

from .main_window import ElectrumWindow
from .util import MyTreeWidget, rate_limited, SortableTreeWidgetItem


class TokenList(MyTreeWidget, util.PrintError):

    class Col(IntEnum):
        """Column numbers. This is to make code in on_update easier to read.
        If you modify these, make sure to modify the column header names in
        the MyTreeWidget constructor."""
        token_id = 0
        label = 1
        quantity = 2
        nfts = 3
        nft_flags = 4
        num_utxos = 5

    class DataRoles(IntEnum):
        """Data roles. Again, to make code in on_update easier to read."""
        wallet_label_key = QtCore.Qt.UserRole
        item_key = QtCore.Qt.UserRole + 1
        token_id = QtCore.Qt.UserRole + 2
        utxos = QtCore.Qt.UserRole + 3
        nft_utxo = QtCore.Qt.UserRole + 4

    filter_columns = [Col.token_id, Col.label]
    default_sort = MyTreeWidget.SortSpec(Col.token_id, QtCore.Qt.DescendingOrder)  # sort by token_id, descending

    def __init__(self, parent: ElectrumWindow):
        assert isinstance(parent, ElectrumWindow)
        columns = [_('TokenID'), _('Label'), _('Total Quantity'), _('NFTs'), _('NFT Flags'), _('Num UTXOs')]
        super().__init__(parent=parent, create_menu=self.create_menu, headers=columns,
                         stretch_column=self.Col.label, deferred_updates=True,
                         save_sort_settings=True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSortingEnabled(True)
        self.wallet = self.parent.wallet
        self.cleaned_up = False
        self.fixed_width = QFont("Courier", QFont().pointSize() - 1)
        self.fixed_width.setFixedPitch(True)
        self.fixed_width.setLetterSpacing(QFont.PercentageSpacing, 90)
        self.smaller_font = QFont()
        self.smaller_font.setPointSize(self.smaller_font.pointSize() - 1)

    def clean_up(self):
        self.cleaned_up = True

    def if_not_dead(func):
        """Boilerplate: Check if cleaned up, and if so, don't execute method"""
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.cleaned_up or not self.wallet or not self.parent:
                return
            else:
                func(self, *args, **kwargs)
        return wrapper

    @rate_limited(1.0, ts_after=True)  # performance tweak -- limit updates to no more than once per second
    def update(self):
        if self.cleaned_up:
            # short-cut return if window was closed and wallet is stopped
            return
        super().update()

    @if_not_dead
    def on_update(self):
        def item_path(item: SortableTreeWidgetItem) -> str:
            """ Recursively builds the path for an item eg 'parent_name/item_name' """
            item_key = item.data(0, self.DataRoles.item_key)
            return item_key if not item.parent() else item_path(item.parent()) + "/" + item_key

        def remember_expanded_items(root: QtWidgets.QTreeWidgetItem) -> Set[str]:
            """ Save the set of expanded items... so that token list updates don't annoyingly collapse our tree list
                widget due to the update. This function recurses. Pass self.invisibleRootItem(). """
            ret: Set[str] = set()
            for i in range(0, root.childCount()):
                it = root.child(i)
                if it and it.childCount():
                    if it.isExpanded():
                        ret.add(item_path(it))
                    ret |= remember_expanded_items(it)  # recurse
            return ret

        def restore_expanded_items(root, item_names: Set[str]):
            """ Recursively restore the expanded state saved previously. Pass self.invisibleRootItem(). """
            for i in range(0, root.childCount()):
                it = root.child(i)
                if it and it.childCount():
                    restore_expanded_items(it, item_names)  # recurse, do leaves first
                    old = bool(it.isExpanded())
                    new = bool(item_path(it) in item_names)
                    if old != new:
                        it.setExpanded(new)

        # Remember selections and previously-expanded items, if any (keeps UI state consistent across refreshes)
        sels = self.selectedItems()
        item_keys_to_re_select = {item.data(0, self.DataRoles.item_key) for item in sels}
        expanded_item_names = remember_expanded_items(self.invisibleRootItem())
        del sels  # avoid keeping reference to about-to-be deleted C++ objects

        self.clear()

        tok_utxos = self.wallet.get_utxos(tokens_only=True)

        tokens: DefaultDict[List[Dict]] = defaultdict(list)  # key: token_id, value: List of utxo dicts
        items_to_re_select: List[SortableTreeWidgetItem] = []

        for utxo in tok_utxos:
            td = utxo['token_data']
            assert isinstance(td, token.OutputData)
            tokens[td.id_hex].append(utxo)

        for token_id, utxo_list in tokens.items():
            token_label_key = f'token_{token_id}'
            item_key = token_label_key
            label = self.wallet.get_label(token_label_key)
            quantity = str(sum(u['token_data'].amount for u in utxo_list))
            nfts = str(len([u for u in utxo_list if u['token_data'].has_nft()]))
            flags = set()
            nft_dict: DefaultDict[List[Dict]] = defaultdict(list)  # key: nft commitment hex, value: list of utxo dicts

            def get_nft_flag(td: token.OutputData) -> Optional[str]:
                if td.is_minting_nft():
                    return _('Minting')
                elif td.is_mutable_nft():
                    return _('Mutable')
                elif td.is_immutable_nft():
                    return _('Immutable')

            for utxo in utxo_list:
                td: token.OutputData = utxo['token_data']
                if td.has_nft():
                    nft_dict[td.commitment.hex()].append(utxo)
                fl = get_nft_flag(td)
                if fl is not None:
                    flags.add(fl)
            nft_flags = ', '.join(sorted(flags))
            num_utxos = str(len(utxo_list))

            item = SortableTreeWidgetItem([token_id, label, quantity, nfts, nft_flags, num_utxos])
            item.setFont(self.Col.token_id, self.fixed_width)
            item.setFont(self.Col.nft_flags, self.smaller_font)
            item.setData(0, self.DataRoles.wallet_label_key, token_label_key)
            item.setData(0, self.DataRoles.item_key, item_key)
            item.setData(0, self.DataRoles.token_id, token_id)
            item.setData(0, self.DataRoles.utxos, utxo_list)
            item.setData(0, self.DataRoles.nft_utxo, None)

            if item_key in item_keys_to_re_select:
                items_to_re_select.append(item)

            for commitment, nft_utxo_list in nft_dict.items():
                for nft_utxo in sorted(nft_utxo_list, key=lambda x: get_nft_flag(x['token_data']) or ''):
                    td: token.OutputData = nft_utxo['token_data']
                    if commitment:
                        name = f"NFT: {commitment}"
                    else:
                        name = "NFT: " + _("zero-length commitment")
                    nft_label_key = f'token_nft_{token_id}_{commitment}'
                    nft_item_key = f'{nft_utxo["prevout_hash"]}:{nft_utxo["prevout_n"]}'
                    label = self.wallet.get_label(nft_label_key)
                    quantity = '-'
                    nfts = '-'
                    nft_flags = get_nft_flag(td) or ''
                    num_utxos = '-'
                    nft_item = SortableTreeWidgetItem([name, label, quantity, nfts, nft_flags, num_utxos])
                    nft_item.setFont(self.Col.token_id, self.fixed_width)
                    nft_item.setData(0, self.DataRoles.wallet_label_key, nft_label_key)
                    nft_item.setData(0, self.DataRoles.item_key, nft_item_key)
                    nft_item.setData(0, self.DataRoles.token_id, token_id)
                    nft_item.setData(0, self.DataRoles.utxos, None)
                    nft_item.setData(0, self.DataRoles.nft_utxo, nft_utxo)
                    item.addChild(nft_item)

                    if nft_item_key in item_keys_to_re_select:
                        items_to_re_select.append(nft_item)

            self.addChild(item)

            # restore selections
            for item in items_to_re_select:
                # NB: Need to select the item at the end because otherwise weird bugs. See #1042.
                item.setSelected(True)

            # Now, at the very end, enforce previous UI state with respect to what was expanded or not. See #1042
            restore_expanded_items(self.invisibleRootItem(), expanded_item_names)

    @if_not_dead
    def create_menu(self, position):
        menu = QMenu()

        selected = self.selectedItems()
        num_selected = len(selected)

        nested_utxos = [item.data(0, self.DataRoles.utxos) for item in selected if
                        item.data(0, self.DataRoles.utxos)]
        nft_utxos = [item.data(0, self.DataRoles.nft_utxo) for item in selected
                     if item.data(0, self.DataRoles.nft_utxo)]
        utxos = []
        for ulist in nested_utxos:
            for u in ulist:
                utxos.append(u)
        del nested_utxos
        utxos = self.dedupe_utxos(utxos + nft_utxos)

        def doCopy(txt):
            txt = txt.strip()
            self.parent.copy_to_clipboard(txt)

        col = self.currentColumn()
        column_title = self.headerItem().text(col)

        if num_selected > 0:
            if num_selected == 1:
                # Single selection
                item = self.itemAt(position)
                if item:
                    nft_utxo = item.data(0, self.DataRoles.nft_utxo)
                    alt_column_title, alt_copy_text = None, None
                    copy_text = item.text(col).strip()
                    if nft_utxo:
                        # NFT child item
                        if col == self.Col.token_id:
                            alt_column_title = _("NFT Hex")
                            alt_copy_text = nft_utxo['token_data'].commitment.hex()
                            copy_text = nft_utxo['token_data'].id_hex
                        if copy_text == '-':
                            copy_text = None
                    else:
                        # Top-level item
                        pass
                    if copy_text:
                        menu.addAction(_("Copy {}").format(column_title), lambda: doCopy(copy_text))
                    if alt_column_title and alt_copy_text:
                        menu.addAction(_("Copy {}").format(alt_column_title), lambda: doCopy(alt_copy_text))
            else:
                # Multi-selection
                if col > -1:
                    texts, alt_copy, alt_copy_text = None, None, None
                    if col == self.Col.token_id:  # token-id column
                        texts, seen_token_ids = list(), set()
                        # We do it this way to preserve order, but also to de-duplicate
                        for item in selected:
                            tid = item.data(0, self.DataRoles.token_id)
                            if tid not in seen_token_ids:
                                seen_token_ids.add(tid)
                                texts.append(tid)
                        if nft_utxos:
                            alt_copy = _("Copy {}").format(_("NFT Hex")) + f" ({len(nft_utxos)})"
                            alt_copy_text = '\n'.join([u['token_data'].commitment.hex() for u in nft_utxos])
                    else:
                        texts = [i.text(col).strip() for i in selected
                                 if i.text(col).strip() and i.text(col).strip() != '-']
                        alt_copy_texts = [i.data(0, self.DataRoles.token_id) + ", " + i.text(col).strip()
                                          for i in selected if i.text(col).strip() and i.text(col).strip() != '-']
                        alt_copy_text = "\n".join(alt_copy_texts)
                        alt_copy = _("Copy {}").format(_('TokenID')) + ", " + column_title + f" ({len(alt_copy_texts)})"
                    if texts:
                        copy_text = '\n'.join(texts)
                        menu.addAction(_("Copy {}").format(column_title) + f" ({len(texts)})",
                                       lambda: doCopy(copy_text))
                    if alt_copy and alt_copy_text:
                        menu.addAction(alt_copy, lambda: doCopy(alt_copy_text))

            menu.addSeparator()
            menu.addAction(QtGui.QIcon(":icons/tab_send.png"),
                           ngettext("Send Token...", "Send Tokens...", num_selected),
                           lambda: self.send_tokens(utxos))

        menu.addAction(QtGui.QIcon(":icons/tab_token.svg"), _("Create Token..."), self.create_new_token)

        menu.exec_(self.viewport().mapToGlobal(position))

    @if_not_dead
    def create_new_token(self):
        self.parent.show_message("Create token is unimplemented!", parent=self.parent)

    @staticmethod
    def dedupe_utxos(utxos: List[Dict]) -> List[Dict]:
        deduped_utxos = []
        seen = set()
        for utxo in utxos:
            key = f"{utxo['prevout_hash']}:{utxo['prevout_n']}"
            if key not in seen:
                seen.add(key)
                deduped_utxos.append(utxo)
        return deduped_utxos

    @if_not_dead
    def send_tokens(self, utxos: List[Dict]):
        utxos = self.dedupe_utxos(utxos)
        assert all(isinstance(u['token_data'], token.OutputData) for u in utxos)
        self.parent.show_message("Send {} token UTXO(s)... unimplemented!"
                                 .format(len(utxos)), parent=self.parent)
