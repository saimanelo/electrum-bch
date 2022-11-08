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
from typing import DefaultDict, List, Optional, Set

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QAbstractItemView, QMenu
from PyQt5.QtGui import QFont

from electroncash.i18n import _
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
            """Recursively builds the path for an item eg 'parent_name/item_name'"""
            label_key = item.data(0, self.DataRoles.wallet_label_key)
            return label_key if not item.parent() else item_path(item.parent()) + "/" + label_key

        def remember_expanded_items(root: QtWidgets.QTreeWidgetItem) -> Set[str]:
            # Save the set of expanded items... so that token list updates don't annoyingly collapse
            # our tree list widget due to the update. This function recurses. Pass self.invisibleRootItem().
            ret: Set[str] = set()
            for i in range(0, root.childCount()):
                it = root.child(i)
                if it and it.childCount():
                    if it.isExpanded():
                        ret.add(item_path(it))
                    ret |= remember_expanded_items(it)  # recurse
            return ret

        def restore_expanded_items(root, item_names: Set[str]):
            # Recursively restore the expanded state saved previously. Pass self.invisibleRootItem().
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
        label_keys_to_re_select = {item.data(0, self.DataRoles.wallet_label_key) for item in sels}
        expanded_item_names = remember_expanded_items(self.invisibleRootItem())
        del sels  # avoid keeping reference to about-to-be deleted C++ objects

        self.clear()

        tok_utxos = self.wallet.get_utxos(tokens_only=True)

        tokens: DefaultDict[List[token.OutputData]] = defaultdict(list)  # key: token_id
        items_to_re_select: List[SortableTreeWidgetItem] = []

        for utxo in tok_utxos:
            td = utxo['token_data']
            assert isinstance(td, token.OutputData)
            tokens[td.id_hex].append(td)

        for token_id, tlist in tokens.items():
            token_label_key = f'token_{token_id}'
            label = self.wallet.get_label(token_label_key)
            quantity = str(sum(t.amount for t in tlist))
            nfts = str(len([t for t in tlist if t.has_nft()]))
            flags = set()
            nft_dict: DefaultDict[List[token.OutputData]] = defaultdict(list)  # key: nft commitment hex

            def get_nft_flag(t: token.OutputData) -> Optional[str]:
                if t.is_minting_nft():
                    return _('Minting')
                elif t.is_mutable_nft():
                    return _('Mutable')
                elif t.is_immutable_nft():
                    return _('Immutable')

            for t in tlist:
                if t.has_nft():
                    nft_dict[t.commitment.hex()].append(t)
                f = get_nft_flag(t)
                if f is not None:
                    flags.add(f)
            nft_flags = ', '.join(sorted(flags))
            num_utxos = str(len(tlist))

            item = SortableTreeWidgetItem([token_id, label, quantity, nfts, nft_flags, num_utxos])
            item.setFont(self.Col.token_id, self.fixed_width)
            item.setFont(self.Col.nft_flags, self.smaller_font)
            item.setData(0, self.DataRoles.wallet_label_key, token_label_key)

            if token_label_key in label_keys_to_re_select:
                items_to_re_select.append(item)

            for commitment, tlist in nft_dict.items():
                for t in sorted(tlist, key=lambda x: get_nft_flag(x) or ''):
                    if commitment:
                        name = f"NFT: {commitment}"
                    else:
                        name = "NFT: " + _("zero-length commitment")
                    nft_label_key = f'token_nft_{token_id}_{commitment}'
                    label = self.wallet.get_label(nft_label_key)
                    quantity = '-'
                    nfts = '-'
                    nft_flags = get_nft_flag(t) or ''
                    num_utxos = '-'
                    nft_item = SortableTreeWidgetItem([name, label, quantity, nfts, nft_flags, num_utxos])
                    nft_item.setFont(self.Col.token_id, self.fixed_width)
                    nft_item.setData(0, self.DataRoles.wallet_label_key, nft_label_key)
                    item.addChild(nft_item)

                    if nft_label_key in label_keys_to_re_select:
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
        menu.exec_(self.viewport().mapToGlobal(position))
