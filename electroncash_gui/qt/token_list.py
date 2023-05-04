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
from .util import MyTreeWidget, rate_limited, SortableTreeWidgetItem, MONOSPACE_FONT


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
        bch_amount = 6
        output_pt = 7

    class DataRoles(IntEnum):
        """Data roles. Again, to make code in on_update easier to read."""
        item_key = QtCore.Qt.UserRole  # This is also the wallet label key
        token_id = QtCore.Qt.UserRole + 1
        utxos = QtCore.Qt.UserRole + 2
        nft_utxo = QtCore.Qt.UserRole + 3

    filter_columns = [Col.token_id, Col.label]
    default_sort = MyTreeWidget.SortSpec(Col.token_id, QtCore.Qt.AscendingOrder)  # sort by token_id, ascending

    amount_heading = _('Amount ({unit})')

    def __init__(self, parent: ElectrumWindow):
        assert isinstance(parent, ElectrumWindow)
        columns = [_('TokenID'), _('Label'), _('Fungible Amount'), _('NFTs'), _('NFT Flags'), _('Num UTXOs'),
                   self.amount_heading.format(unit=parent.base_unit()), _('Output Point')]
        super().__init__(parent=parent, create_menu=self.create_menu, headers=columns,
                         stretch_column=self.Col.label, deferred_updates=True,
                         save_sort_settings=True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSortingEnabled(True)
        self.wallet = self.parent.wallet
        self.cleaned_up = False
        self.fixed_width = QFont(MONOSPACE_FONT, QFont().pointSize() - 1)
        self.fixed_width.setFixedPitch(True)
        self.fixed_width.setStretch(QFont.SemiCondensed)
        self.fixed_width_smaller = QFont(MONOSPACE_FONT, QFont().pointSize() - 1)
        self.fixed_width_smaller.setFixedPitch(True)
        self.fixed_width_smaller.setStretch(QFont.Condensed)
        self.fixed_width_larger = QFont(MONOSPACE_FONT, QFont().pointSize())
        self.fixed_width_larger.setFixedPitch(True)
        self.smaller_font = QFont()
        self.smaller_font.setPointSize(self.smaller_font.pointSize() - 1)
        self.smaller_font.setLetterSpacing(QFont.PercentageSpacing, 90)

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

    @classmethod
    def get_outpoint_shortname(cls, utxo):
        return cls.elide(utxo['prevout_hash'], 12) + ':' + str(utxo['prevout_n'])

    @staticmethod
    def get_outpoint_longname(utxo):
        return f"{utxo['prevout_hash']}:{utxo['prevout_n']}"

    @staticmethod
    def elide(s: str, elide_threshold=32) -> str:
        if len(s) > elide_threshold:
            n = max(elide_threshold // 2, 0)
            return s[:n] + '…' + s[-n:]
        return s

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

        # Pick up changes to the configured base_unit and update the amount column
        self.headerItem().setText(self.Col.bch_amount, self.amount_heading.format(unit=self.parent.base_unit()))

        tok_utxos = self.wallet.get_utxos(tokens_only=True)

        tokens: DefaultDict[List[Dict]] = defaultdict(list)  # key: token_id, value: List of utxo dicts
        items_to_re_select: List[SortableTreeWidgetItem] = []
        # token_id_hex -> <commitment_hex>|"ft_only" -> list of utxos
        tokens_grouped: DefaultDict[DefaultDict[List[Dict]]] = defaultdict(lambda: defaultdict(list))

        for utxo in tok_utxos:
            td = utxo['token_data']
            assert isinstance(td, token.OutputData)
            token_id = td.id_hex
            tokens[token_id].append(utxo)
            if not td.has_nft():
                # Special group -- fungible-only utxos
                tokens_grouped[token_id]["ft_only"].append(utxo)
            else:
                # Otherwise group each token_id by the commitment bytes
                tokens_grouped[token_id][td.commitment.hex()].append(utxo)

        def get_nft_flag(td: token.OutputData) -> Optional[str]:
            if td.is_minting_nft():
                return _('Minting')
            elif td.is_mutable_nft():
                return _('Mutable')
            elif td.is_immutable_nft():
                return _('Immutable')

        def set_fonts(item: SortableTreeWidgetItem):
            for col in (self.Col.token_id, self.Col.quantity, self.Col.bch_amount):
                txt = item.text(col)
                if col == self.Col.token_id and txt.startswith(_("Fungible-Only")):
                    continue  # Don't set the font for the "Fungible-Only" entries
                if len(txt) > 64:
                    item.setFont(col, self.fixed_width_smaller)
                elif len(txt) > 32:
                    item.setFont(col, self.fixed_width)
                else:
                    item.setFont(col, self.fixed_width_larger)
            item.setFont(self.Col.nft_flags, self.smaller_font)
            item.setFont(self.Col.output_pt, self.fixed_width)
            # Lastly, realign the quantity, num_nfts, and num_utxos columns
            for col, align in ((self.Col.quantity, QtCore.Qt.AlignRight),
                               (self.Col.nfts, QtCore.Qt.AlignCenter),
                               (self.Col.num_utxos, QtCore.Qt.AlignCenter)):
                item.setTextAlignment(col, align)

        def add_utxo_item(parent, utxo, name, label, item_key):
            td = utxo['token_data']
            tid = td.id_hex
            amt = str(td.amount)
            nft_flags = get_nft_flag(td) or ""
            num_nfts = str(int(td.has_nft()))
            num_utxos = "1"
            outpt_shortname = self.get_outpoint_shortname(utxo)
            bch_amt = self.parent.format_amount(utxo['value'], is_diff=False, whitespaces=True)
            stwi = SortableTreeWidgetItem([name, label, amt, num_nfts, nft_flags, num_utxos, bch_amt, outpt_shortname])
            set_fonts(stwi)
            tt = self.get_outpoint_longname(utxo) + "\n"
            if utxo['height'] > 0:
                tt += _("Confirmed in block {height}").format(height=utxo['height'])
            else:
                tt += _("Unconfirmed")
            stwi.setToolTip(self.Col.output_pt, tt)
            stwi.setData(0, self.DataRoles.item_key, item_key)
            stwi.setData(0, self.DataRoles.token_id, tid)
            stwi.setData(0, self.DataRoles.utxos, [utxo])
            stwi.setData(0, self.DataRoles.nft_utxo, utxo if td.has_nft() else None)
            stwi.setToolTip(self.Col.label, label)  # Just in case label got elided
            parent.addChild(stwi)
            if item_key in item_keys_to_re_select:
                items_to_re_select.append(stwi)

        for token_id, dd in tokens_grouped.items():
            utxo_list = tokens[token_id]
            key_prefix = f'token_{token_id}'
            item_key = key_prefix
            label = self.wallet.get_label(item_key) or ""
            quantity = str(sum(u['token_data'].amount for u in utxo_list))
            num_nfts = sum(1 for u in utxo_list if u['token_data'].has_nft())
            nfts = str(num_nfts)
            flags = {get_nft_flag(u['token_data']) for u in utxo_list}
            flags.discard(None)  # Non-nft's add "None" to this set. discard
            nft_flags = ', '.join(sorted(flags))
            num_utxos = str(sum(len(ul) for ul in dd.values()))
            bch_amt = self.parent.format_amount(sum(x['value'] for x in utxo_list), is_diff=False, whitespaces=True)

            item = SortableTreeWidgetItem([token_id, label, quantity, nfts, nft_flags, num_utxos, bch_amt, ""])
            set_fonts(item)
            item.setData(0, self.DataRoles.item_key, item_key)
            item.setData(0, self.DataRoles.token_id, token_id)
            item.setData(0, self.DataRoles.utxos, utxo_list)
            item.setData(0, self.DataRoles.nft_utxo, None)
            if item_key in item_keys_to_re_select:
                items_to_re_select.append(item)

            # Do fungibles first
            dd = dd.copy()
            ft_only_utxo_list = dd.pop("ft_only", [])
            ft_only_utxo_list = sorted(ft_only_utxo_list, key=lambda u: -u['token_data'].amount)  # Sort by amount, desc

            name = _("Fungible-Only")
            if len(ft_only_utxo_list) == 1:
                utxo = ft_only_utxo_list[0]
                item_key = key_prefix + "_" + self.get_outpoint_longname(utxo)
                add_utxo_item(item, utxo, name,
                              self.wallet.get_label(item_key) or label,
                              item_key)
            elif ft_only_utxo_list:
                item_key = key_prefix + "_ft_only"
                ft_parent_label = self.wallet.get_label(item_key) or label
                ft_amt = str(sum(u['token_data'].amount for u in ft_only_utxo_list))
                bch_amt = self.parent.format_amount(sum(x['value'] for x in ft_only_utxo_list), is_diff=False,
                                                    whitespaces=True)
                ft_parent = SortableTreeWidgetItem([name, ft_parent_label, ft_amt, "0", "",
                                                    str(len(ft_only_utxo_list)), bch_amt, ""])
                set_fonts(ft_parent)
                ft_parent.setData(0, self.DataRoles.item_key, item_key)
                ft_parent.setData(0, self.DataRoles.token_id, token_id)
                ft_parent.setData(0, self.DataRoles.utxos, ft_only_utxo_list)
                ft_parent.setData(0, self.DataRoles.nft_utxo, None)
                if item_key in item_keys_to_re_select:
                    items_to_re_select.append(ft_parent)

                for utxo in ft_only_utxo_list:
                    item_key2 = key_prefix + "_" + self.get_outpoint_longname(utxo)
                    add_utxo_item(ft_parent, utxo, name,
                                  self.wallet.get_label(item_key2) or ft_parent_label,
                                  item_key2)

                item.addChild(ft_parent)

            # Do NFTs next; iterate sorted by commitment, asc
            for commitment_hex, utxo_list in sorted(dd.items(), key=lambda tup: tup[0]):
                utxo_list = sorted(utxo_list, key=lambda u: -u['token_data'].amount)  # Sort by amount, desc
                if not utxo_list:
                    continue

                if commitment_hex:
                    name = f"NFT: {commitment_hex}"
                else:
                    name = "NFT: " + _("zero-length commitment")

                if len(utxo_list) == 1:
                    utxo = utxo_list[0]
                    item_key = key_prefix + "_" + self.get_outpoint_longname(utxo)
                    add_utxo_item(item, utxo, name,
                                  self.wallet.get_label(item_key) or label,
                                  item_key)
                else:
                    item_key = key_prefix + "_nft_" + commitment_hex
                    ft_amt = str(sum(u['token_data'].amount for u in utxo_list))
                    nfts = str(sum(1 for u in utxo_list if u['token_data'].has_nft()))
                    nft_flags = ', '.join(sorted({get_nft_flag(u['token_data']) for u in utxo_list}))
                    num_utxos = str(len(utxo_list))
                    bch_amt = self.parent.format_amount(sum(x['value'] for x in utxo_list), is_diff=False,
                                                        whitespaces=True)
                    parent_label = self.wallet.get_label(item_key) or label
                    nft_parent = SortableTreeWidgetItem([name, parent_label, ft_amt, nfts, nft_flags, num_utxos,
                                                         bch_amt, ""])
                    set_fonts(nft_parent)
                    nft_parent.setData(0, self.DataRoles.item_key, item_key)
                    nft_parent.setData(0, self.DataRoles.token_id, token_id)
                    nft_parent.setData(0, self.DataRoles.utxos, utxo_list)
                    nft_parent.setData(0, self.DataRoles.nft_utxo, None)
                    if item_key in item_keys_to_re_select:
                        items_to_re_select.append(nft_parent)

                    for utxo in utxo_list:
                        item_key2 = key_prefix + "_" + self.get_outpoint_longname(utxo)
                        add_utxo_item(nft_parent, utxo, name,
                                      self.wallet.get_label(item_key2) or parent_label,
                                      item_key2)

                    item.addChild(nft_parent)

            self.addChild(item)

        # Now, at the very end, enforce previous UI state with respect to what was expanded or not. See #1042
        restore_expanded_items(self.invisibleRootItem(), expanded_item_names)

        # restore selections
        for item in items_to_re_select:
            # NB: Need to select the item at the end because otherwise weird bugs. See #1042.
            item.setSelected(True)

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

        def do_copy(txt):
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
                    utxos = item.data(0, self.DataRoles.utxos)
                    is_leaf_utxo = len(utxos) == 1 and item.childCount() == 0
                    if col == self.Col.token_id:
                        copy_text = item.data(0, self.DataRoles.token_id)
                    elif col == self.Col.output_pt and is_leaf_utxo:
                        copy_text = self.get_outpoint_longname(utxos[0])
                    else:
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
                        menu.addAction(_("Copy {}").format(column_title), lambda: do_copy(copy_text))
                    if alt_column_title and alt_copy_text:
                        menu.addAction(_("Copy {}").format(alt_column_title), lambda: do_copy(alt_copy_text))
                    if is_leaf_utxo:
                        txid = utxos[0]['prevout_hash']
                        tx = self.wallet.transactions.get(txid, None)

                        def do_show_tx():
                            self.parent.show_transaction(tx, tx_desc=self.wallet.get_label(txid) or None)
                        if tx:
                            menu.addAction(_("Details"), do_show_tx)
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
                        def get_text(item):
                            if col == self.Col.output_pt and item.childCount() == 0:
                                ul = item.data(0, self.DataRoles.utxos)
                                if len(ul) == 1:
                                    return self.get_outpoint_longname(ul[0])
                            return item.text(col).strip()
                        texts = [get_text(i) for i in selected if get_text(i) and get_text(i) != '-']
                        alt_copy_texts = [i.data(0, self.DataRoles.token_id) + ", " + get_text(i)
                                          for i in selected if get_text(i) and get_text(i) != '-']
                        alt_copy_text = "\n".join(alt_copy_texts)
                        alt_copy = _("Copy {}").format(_('TokenID')) + ", " + column_title + f" ({len(alt_copy_texts)})"
                    if texts:
                        copy_text = '\n'.join(texts)
                        menu.addAction(_("Copy {}").format(column_title) + f" ({len(texts)})",
                                       lambda: do_copy(copy_text))
                    if alt_copy and alt_copy_text:
                        menu.addAction(alt_copy, lambda: do_copy(alt_copy_text))

            menu.addSeparator()
            menu.addAction(QtGui.QIcon(":icons/tab_send.png"),
                           ngettext("Send Token...", "Send Tokens...", num_selected),
                           lambda: self.send_tokens(utxos))

        menu.addAction(QtGui.QIcon(":icons/tab_token.svg"), _("Create Token..."), self.create_new_token)

        menu.exec_(self.viewport().mapToGlobal(position))

    @if_not_dead
    def create_new_token(self):
        self.parent.show_create_new_token_dialog()

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

    @if_not_dead
    def update_labels(self):
        self.update()

    def on_doubleclick(self, item, column):
        if self.permit_edit(item, column):
            super().on_doubleclick(item, column)
        else:
            utxos = item.data(0, self.DataRoles.utxos)
            if len(utxos) == 1 and item.childCount() == 0:  # Leaf utxo item, show transaction
                tx_hash = utxos[0]['prevout_hash']
                tx = self.wallet.transactions.get(tx_hash)
                if tx:
                    label = self.wallet.get_label(tx_hash) or None
                    self.parent.show_transaction(tx, label)
