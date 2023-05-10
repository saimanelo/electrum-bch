#!/usr/bin/env python3
#
# Electron Cash - lightweight Bitcoin Cash client
# Copyright (C) 2023 The Electron Cash Developers
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

import copy
import math
from collections import defaultdict
from enum import IntEnum
from typing import Any, Callable, DefaultDict, Dict, List, Optional, Set

from PyQt5 import QtCore, QtGui, QtWidgets

from electroncash import address, networks, token, util, wallet
from electroncash.i18n import _
from .amountedit import BTCAmountEdit
from .fee_slider import FeeSlider
from .main_window import ElectrumWindow
from .qrtextedit import ScanQRTextEdit
from .token_meta import TokenMetaQt
from .util import ColorScheme, HelpLabel, OnDestroyedMixin, PrintError, WindowModalDialog


class SendTokenForm(WindowModalDialog, PrintError, OnDestroyedMixin):

    class ColsTok(IntEnum):
        token_id = 0
        nfts = 1
        amount = 2
        amount_send = 3

    class ColsNFT(IntEnum):
        selected = 0
        token_id = 1
        commitment = 2
        flags = 3

    class DataRoles(IntEnum):
        token_id = QtCore.Qt.UserRole
        output_point = QtCore.Qt.UserRole + 1
        receives_nft_count_updates = QtCore.Qt.UserRole + 2
        receives_nft_flag_updates = QtCore.Qt.UserRole + 3

    headers_tok = [_("Category ID"), _("NFTs to Send"), _("Fungible Amount"), _("Fungible Amount to Send")]
    headers_nft = [_("Send"), _("Category ID"), _("Commitment"), _("Flags")]

    def __init__(self, parent: ElectrumWindow, token_utxos: List[dict],
                 *, broadcast_callback: Optional[Callable[[bool], Any]] = None,
                 edit_mode=False):
        assert isinstance(parent, ElectrumWindow)
        title = (_("Send Tokens") if not edit_mode else _("Edit Tokens")) + " - " + parent.wallet.basename()
        super().__init__(parent=parent, title=title)
        PrintError.__init__(self)
        OnDestroyedMixin.__init__(self)
        self.fully_constructed = False
        util.finalization_print_error(self)
        self.setWindowIcon(QtGui.QIcon(":icons/tab_send.png"))
        self.parent = parent
        self.token_meta: TokenMetaQt = parent.token_meta
        self.wallet: wallet.Abstract_Wallet = self.parent.wallet
        self.utxos_by_name: Dict[str, dict] = dict()
        self.token_utxos: DefaultDict[str, List[str]] = defaultdict(list)  # tokenid -> unique sorted list of utxonames
        self.token_nfts: DefaultDict[str, List[str]] = defaultdict(list)  # tokenid -> list of utxonames
        self.token_fungible_only: DefaultDict[str, List[str]] = defaultdict(list)
        self.token_fungible_totals: DefaultDict[str, int] = defaultdict(int)  # tokenid -> fungible total
        self.token_nfts_selected: DefaultDict[str, Set[str]] = defaultdict(set)  # tokenid -> set of selected utxonames
        self.token_fungible_to_spend: DefaultDict[str, int] = defaultdict(int)  # tokenid -> amount
        self.nfts_to_edit: DefaultDict[str, Optional[bytes]] = defaultdict(bytes)  # utxoname -> new commitment bytes
        self.broadcast_callback = broadcast_callback
        self.icon_baton = QtGui.QIcon(":icons/baton.png")
        self.icon_mutable = QtGui.QIcon(":icons/mutable.png")
        self.edit_mode = edit_mode

        # Setup data source; iterate over a sorted list of utxos
        def sort_func(u):
            td: token.OutputData = u['token_data']
            return td.id, td.commitment, td.bitfield & 0x0f, self.get_outpoint_longname(u)
        sorted_utxos = sorted(token_utxos, key=sort_func)

        for utxo in sorted_utxos:
            td: token.OutputData = utxo['token_data']
            assert isinstance(td, token.OutputData)
            tid = td.id_hex
            name = self.get_outpoint_longname(utxo)
            if name in self.utxos_by_name:
                # skip dupes
                assert self.utxos_by_name[name] == utxo
                continue
            self.utxos_by_name[name] = utxo
            self.token_utxos[tid].append(name)
            if td.has_nft():
                self.token_nfts[tid].append(name)
                # Start out with nothing selected
                self.token_nfts_selected[tid].clear()
            else:
                assert td.has_amount()
                self.token_fungible_only[tid].append(name)
            self.token_fungible_totals[tid] += td.amount

        # Build UI
        vbox = QtWidgets.QVBoxLayout(self)

        self.gb_ft = gb = QtWidgets.QGroupBox(_("Tokens to Send"))
        vbox_gb = QtWidgets.QVBoxLayout(gb)

        self.tw_tok = tw = QtWidgets.QTreeWidget()
        tw.setAlternatingRowColors(True)
        tw.setSortingEnabled(False)
        tw.setTextElideMode(QtCore.Qt.ElideMiddle)
        tw.setHeaderLabels(self.headers_tok)
        tw.header().setSectionResizeMode(self.ColsTok.amount_send, QtWidgets.QHeaderView.Stretch)
        vbox_gb.addWidget(tw)

        self.gb_nft = gb_nft = QtWidgets.QGroupBox(_("NFTs to Send") if not self.edit_mode else _("NFTs to Edit"))
        gb_nft_vbox = QtWidgets.QVBoxLayout(gb_nft)

        self.tw_nft = tw = QtWidgets.QTreeWidget()
        tw.setAlternatingRowColors(True)
        tw.setSortingEnabled(False)
        tw.setTextElideMode(QtCore.Qt.ElideMiddle)
        if self.edit_mode:
            self.headers_nft = [_("Selected")] + self.headers_nft[1:]
        tw.setHeaderLabels(self.headers_nft)
        if self.edit_mode:
            tw.header().setSectionResizeMode(self.ColsNFT.commitment, QtWidgets.QHeaderView.Stretch)
        else:
            tw.header().setSectionResizeMode(self.ColsNFT.flags, QtWidgets.QHeaderView.Stretch)
        gb_nft_vbox.addWidget(tw)

        self.rebuild_nfts_to_send_treewidget()
        self.rebuild_tokens_to_send_treewidget()

        # Receive notification and update nft selected sets when user clicks the NFT widget
        self.tw_nft.itemChanged.connect(self.on_nft_item_changed)

        vbox.addWidget(gb)
        vbox.addWidget(gb_nft)

        # Pay To
        gb = QtWidgets.QGroupBox(_("Pay To"))
        vbox_payto = QtWidgets.QVBoxLayout(gb)
        self.te_payto = te = ScanQRTextEdit()

        vbox_payto.addWidget(te)
        te.setPlaceholderText(networks.net.CASHADDR_PREFIX + ":" + "...")
        te.textChanged.connect(self.on_ui_state_changed)

        vbox.addWidget(gb)
        self._adjust_te_payto_size()
        if self.edit_mode:
            gb.setHidden(True)
            self.te_payto.setText(self.wallet.get_unused_address(for_change=True).to_token_string())

        # BCH to send plus description
        hbox = QtWidgets.QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)  # No inset
        # Additional BCH to Send
        self._setup_additional_bch_gbox(hbox)
        # Description
        gb_desc = QtWidgets.QGroupBox(_("Description"))
        gb_desc.setToolTip(_("Enter an optional label for the transaction"))
        vbox_gb_desc = QtWidgets.QVBoxLayout(gb_desc)
        self.te_desc = QtWidgets.QPlainTextEdit()
        vbox_gb_desc.addWidget(self.te_desc)
        self.te_desc.setWordWrapMode(QtGui.QTextOption.WrapAnywhere)
        self.te_desc.setPlaceholderText(_("Memo") + "...")
        hbox.addWidget(gb_desc)

        vbox.addLayout(hbox)

        # Bottom buttons
        hbox = QtWidgets.QHBoxLayout()
        but_clear = QtWidgets.QPushButton(_("Clear"))
        but_clear.clicked.connect(self.clear_form)
        hbox.addWidget(but_clear)
        self.lbl_status_msg = l = QtWidgets.QLabel()
        hbox.addWidget(l, 1, QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
        spacer = QtWidgets.QSpacerItem(0, 0, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        hbox.addItem(spacer)
        but_cancel = QtWidgets.QPushButton(_("Cancel"))
        but_cancel.clicked.connect(self.close)
        hbox.addWidget(but_cancel)
        self.but_preview_tx = QtWidgets.QPushButton(_("Preview Tx..."))
        self.but_preview_tx.setDefault(True)
        self.but_preview_tx.clicked.connect(self.on_preview_tx)
        hbox.addWidget(self.but_preview_tx)

        vbox.addLayout(hbox)

        self.fully_constructed = True
        self.on_ui_state_changed()

        self.resize(640, 480)

    def _adjust_te_payto_size(self):
        te = self.te_payto
        font_spacing = QtGui.QFontMetrics(te.document().defaultFont()).lineSpacing()
        margins = te.contentsMargins()
        document_margin = te.document().documentMargin()
        vertical_margins = margins.top() + margins.bottom()
        vertical_margins += te.frameWidth() * 2
        vertical_margins += int(document_margin * 2)

        height_min = font_spacing + vertical_margins
        te.setMinimumHeight(height_min)
        te.setMaximumHeight(int(height_min * 1.5))

    def _setup_additional_bch_gbox(self, parent_layout: QtWidgets.QLayout):
        gb = QtWidgets.QGroupBox(_("Additional BCH to Send"))
        grid = QtWidgets.QGridLayout(gb)
        row, col, n_cols = 0, 0, 5
        msg = (_('Additional amount to be sent along with the tokens.') + '\n\n'
               # The below text comes from the main_window.
               + _('The amount will be displayed in red if you do not have enough funds in your wallet.') + ' '
               + _('Note that if you have frozen some of your addresses, the available funds will be lower than your'
                   ' total balance.'))
        l = HelpLabel(_("Amount"), msg)
        grid.addWidget(l, row, col)
        col += 1
        self.amount_e = BTCAmountEdit(self.parent.get_decimal_point)
        self.amount_e.setFixedWidth(150)
        grid.addWidget(self.amount_e, row, col)
        l.setBuddy(self.amount_e)
        col += 1

        def spend_max(checked):
            if checked:
                self.on_ui_state_changed()
        self.cb_max = QtWidgets.QCheckBox(_("&Max"))
        grid.addWidget(self.cb_max, row, col)
        self.cb_max.clicked.connect(spend_max)
        col += 1

        def on_text_edited():
            if self.cb_max.isChecked():
                self.cb_max.setChecked(False)  # will call on_ui_state_changed
            else:
                self.on_ui_state_changed()
        self.amount_e.textEdited.connect(on_text_edited)

        spacer = QtWidgets.QSpacerItem(0, 0, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        grid.addItem(spacer, row, col)

        row += 1
        col = 0
        msg = (_('Bitcoin Cash transactions are in general not free. A transaction fee is paid by the sender of the'
                 ' funds.') + '\n\n'
               + _('Generally, a fee of 1.0 sats/B is a good minimal rate to ensure your transaction will make it into'
                   ' the next block.'))
        fee_e_label = HelpLabel(_('F&ee'), msg)
        grid.addWidget(fee_e_label)
        col += 1

        self.fee_rate = 1000  # this gets quickly overwritten below

        def fee_cb(dyn, pos, fee_rate):
            self.fee_rate = fee_rate
            self.on_ui_state_changed()

        self.fee_slider = FeeSlider(self.parent, self.parent.config, fee_cb)
        self.fee_slider.setFixedWidth(140)
        grid.addWidget(self.fee_slider, row, col)
        fee_e_label.setBuddy(self.fee_slider)
        self.fee_slider.moved(self.fee_slider.value())  # Ensure callback fires at least once

        parent_layout.addWidget(gb)
        if self.edit_mode:
            gb.setHidden(True)

    def have_fts(self) -> bool:
        return sum(amt for amt in self.token_fungible_totals.values()) > 0

    def have_nfts(self) -> bool:
        return sum(len(u) for u in self.token_nfts.values()) > 0

    def get_nfts_selected_str(self, tid: str) -> str:
        nft_total = len(self.token_nfts.get(tid, []))
        nfts_selected = len(self.token_nfts_selected.get(tid, set()))
        return f"{nfts_selected}/{nft_total}"

    def on_nft_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int):
        tid = item.data(0, self.DataRoles.token_id)
        name = item.data(0, self.DataRoles.output_point)
        # Update NFTs selected set and counts in UI
        if tid and name and column == self.ColsNFT.selected:
            checked = item.checkState(column) == QtCore.Qt.Checked
            if checked:
                self.token_nfts_selected[tid].add(name)
            else:
                self.token_nfts_selected[tid].discard(name)
            self.update_tokens_to_send_nft_count(tid)
            self.on_ui_state_changed()

    def update_tokens_to_send_nft_flags(self, tid: str):
        """Intended to be a callback for a specific token id -- updates the NFTs x/y column"""
        tw = self.tw_nft
        for i in range(tw.topLevelItemCount()):
            item = tw.topLevelItem(i)
            if (item.data(0, self.DataRoles.token_id) == tid
                    and item.data(0, self.DataRoles.receives_nft_flag_updates)):
                flags = set()
                for index in range(item.childCount()):
                    child = item.child(index)
                    name = child.data(0, self.DataRoles.output_point)
                    if name and name in self.token_nfts_selected.get(tid, set()):
                        flags.add(child.text(self.ColsNFT.flags))
                flags = ', '.join(flags)
                if flags:
                    flags = _("Selected") + ": " + flags
                item.setText(self.ColsNFT.flags, flags)
                return

    def update_tokens_to_send_nft_count(self, tid: str):
        """Intended to be a callback for a specific token id -- updates the NFTs x/y column"""
        tws = [(self.tw_tok, self.ColsTok.nfts), (self.tw_nft, self.ColsNFT.selected)]
        for tw, col in tws:
            if tw is None:
                continue
            for i in range(tw.topLevelItemCount()):
                item = tw.topLevelItem(i)
                if (item.data(0, self.DataRoles.token_id) == tid
                        and item.data(0, self.DataRoles.receives_nft_count_updates)):
                    item.setText(col, self.get_nfts_selected_str(tid))
                    break

    def rebuild_tokens_to_send_treewidget(self):
        tw = self.tw_tok
        saved_amts = self.token_fungible_to_spend
        tw.clear()

        for tid, amt in self.token_fungible_totals.items():
            try:
                if amt <= 0:
                    # Skip displaying rows in this table for tokens that have no fungibles
                    continue
                item = QtWidgets.QTreeWidgetItem([tid, "", str(amt), ""])
                item.setIcon(self.ColsTok.token_id, self.token_meta.get_icon(tid))
                item.setToolTip(self.ColsTok.token_id, item.text(self.ColsTok.token_id))
                item.setToolTip(self.ColsTok.amount, item.text(self.ColsTok.amount))
                item.setData(0, self.DataRoles.token_id, tid)
                item.setData(0, self.DataRoles.receives_nft_count_updates, True)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
                tw.addTopLevelItem(item)
            finally:
                # Update NFTs M/N column (must be done after add, and even if we didn't add anything)
                self.update_tokens_to_send_nft_count(tid)
            w = QtWidgets.QWidget()
            w.setToolTip(_("Specify fungible token amount to be sent in the transaction"))
            hbox = QtWidgets.QHBoxLayout(w)
            hbox.setContentsMargins(0, 0, 0, 0)
            le = QtWidgets.QLineEdit()
            le.setObjectName("le")  # so we can find it later
            le.setText(str(saved_amts.get(tid, 0)))

            def on_edit(amt=amt, tid=tid, le=le):
                try:
                    val = int(le.text())
                    if val < 0:
                        val = 0
                    elif val > amt:
                        val = amt
                    self.token_fungible_to_spend[tid] = val
                except ValueError:
                    pass
                le.setText(str(self.token_fungible_to_spend[tid]))
                self.on_ui_state_changed()
            le.editingFinished.connect(on_edit)
            hbox.addWidget(le)

            def on_max(b, amt=amt, tid=tid, le=le):
                le.setText(str(amt))
                on_edit(amt=amt, tid=tid, le=le)

            def on_clear(b, amt=amt, tid=tid, le=le):
                le.setText("0")
                on_edit(amt=amt, tid=tid, le=le)
            but = QtWidgets.QToolButton()
            but.clicked.connect(on_max)
            but.setText(_("Max"))
            but.setObjectName("max")
            but.setToolTip("Set to full amount available")
            hbox.addWidget(but)
            but2 = QtWidgets.QToolButton()
            but2.clicked.connect(on_clear)
            but2.setText(_("Clear"))
            but2.setObjectName("clear")
            but2.setToolTip("Set to 0")
            hbox.addWidget(but2)
            w.setAutoFillBackground(True)
            tw.setItemWidget(item, self.ColsTok.amount_send, w)

        if self.edit_mode or not self.have_fts():
            # Hide the fungible token box in edit mode or if no fungibles
            self.gb_ft.setHidden(True)

    def rebuild_nfts_to_send_treewidget(self):
        tw = self.tw_nft
        tw.clear()

        def add_leaf_item(parent: QtWidgets.QTreeWidgetItem, tid, name) -> QtWidgets.QTreeWidgetItem:
            utxo = self.get_utxo(name)
            td = utxo['token_data']
            assert isinstance(td, token.OutputData)
            commitment_hex = td.commitment.hex()
            item = QtWidgets.QTreeWidgetItem(["", tid, commitment_hex, token.get_nft_flag_text(td)])
            item.setIcon(self.ColsNFT.token_id, self.token_meta.get_icon(tid))
            item.setToolTip(self.ColsNFT.token_id, tid)
            if not self.edit_mode:
                item.setToolTip(self.ColsNFT.selected, _("Check to send this NFT"))
                item.setToolTip(self.ColsNFT.commitment, commitment_hex
                                if commitment_hex else _("zero-length commitment"))
            else:
                item.setToolTip(self.ColsNFT.selected, _("Check to edit this NFT"))
                item.setToolTip(self.ColsNFT.commitment, "Enter an even number of hex characters")
            item.setFlags((item.flags() | QtCore.Qt.ItemIsUserCheckable) & ~QtCore.Qt.ItemIsSelectable)
            item.setData(0, self.DataRoles.token_id, tid)
            item.setData(0, self.DataRoles.output_point, name)
            if td.is_minting_nft():
                item.setIcon(self.ColsNFT.flags, self.icon_baton)
            elif td.is_mutable_nft():
                item.setIcon(self.ColsNFT.flags, self.icon_mutable)
            parent.addChild(item)
            item.setCheckState(self.ColsNFT.selected, QtCore.Qt.Checked)  # Need to call this at least once to make checkbox appear
            if name not in self.token_nfts_selected.get(tid, set()):
                item.setCheckState(self.ColsNFT.selected, QtCore.Qt.Unchecked)

            if self.edit_mode:
                w = QtWidgets.QWidget()
                w.setToolTip(item.toolTip(self.ColsNFT.commitment))
                hbox = QtWidgets.QHBoxLayout(w)
                hbox.setContentsMargins(0, 0, 0, 0)
                le = QtWidgets.QLineEdit()
                le.setObjectName("le_" + name)  # so we can find it later
                le.setText(commitment_hex)

                def on_text_changed(text, item=item, le=le, commitment=td.commitment, name=name):
                    try:
                        new_commitment = bytes.fromhex(text)
                        if new_commitment != commitment:
                            color = ColorScheme.BLUE
                            item.setCheckState(self.ColsNFT.selected, QtCore.Qt.Checked)
                            self.nfts_to_edit[name] = new_commitment
                        else:
                            color = ColorScheme.DEFAULT
                            item.setCheckState(self.ColsNFT.selected, QtCore.Qt.Unchecked)
                            self.nfts_to_edit.pop(name, None)
                    except ValueError:
                        color = ColorScheme.RED
                        item.setCheckState(self.ColsNFT.selected, QtCore.Qt.Unchecked)
                        self.nfts_to_edit[name] = None  # Indicate that we have an error
                    le.setStyleSheet(color.as_stylesheet())
                    self.on_ui_state_changed()
                le.textChanged.connect(on_text_changed)
                hbox.addWidget(le)

                def on_reset(b, commitment_hex=commitment_hex, le=le):
                    is_same = le.text() == commitment_hex
                    if is_same:
                        # If same, above slot won't fire when calling setText(), so we force it to fire
                        on_text_changed(commitment_hex)
                    else:
                        # Otherwise modify text and slot fires
                        le.setText(commitment_hex)
                but = QtWidgets.QToolButton()
                but.clicked.connect(on_reset)
                but.setText(_("Reset"))
                but.setObjectName("reset_" + name)
                but.setToolTip("Reset to original commitment")
                hbox.addWidget(but)
                w.setAutoFillBackground(True)
                tw.setItemWidget(item, self.ColsNFT.commitment, w)

            return item

        for tid, names in self.token_nfts.items():
            if not names:
                # Defensive programming: should never happen
                continue
            if len(names) == 1:
                # This group has only 1 item, push to top-level and don't build a sub-item
                item = add_leaf_item(tw.invisibleRootItem(), tid, names[0])
                # Subscribe to counts label updates
                item.setData(0, self.DataRoles.receives_nft_count_updates, True)
                continue
            # This group has more than 1 item, build a subgrouping
            parent = QtWidgets.QTreeWidgetItem(["", tid, "", ""])
            parent.setIcon(self.ColsNFT.token_id, self.token_meta.get_icon(tid))
            parent.setToolTip(self.ColsNFT.token_id, tid)
            parent.setFlags((parent.flags() | QtCore.Qt.ItemIsAutoTristate | QtCore.Qt.ItemIsUserCheckable)
                            & ~QtCore.Qt.ItemIsSelectable)
            parent.setData(0, self.DataRoles.token_id, tid)
            parent.setData(0, self.DataRoles.receives_nft_count_updates, True)  # Subscribe to update of counts label
            parent.setData(0, self.DataRoles.receives_nft_flag_updates, True)  # Subscribe to updates of Flags label
            parent.setCheckState(0, QtCore.Qt.Checked)  # Needs to be called once to make checkbox appear
            nfts_selected = self.token_nfts_selected.get(tid, set())
            nchkd = len(nfts_selected)
            ntot = len(names)
            if nchkd == 0:
                parent.setCheckState(0, QtCore.Qt.Unchecked)
            elif nchkd < ntot:
                parent.setCheckState(0, QtCore.Qt.PartiallyChecked)
            tw.addTopLevelItem(parent)
            for name in names:
                add_leaf_item(parent, tid, name)

        if tw.topLevelItemCount() == 1:
            # Auto-expand if only 1 item
            item = tw.topLevelItem(0)
            if item.childCount() > 0:
                item.setExpanded(True)

        # if we have no NFTs, hide this widget completely
        self.gb_nft.setHidden(not self.have_nfts())

    def diagnostic_name(self):
        dn = super().diagnostic_name()
        w = getattr(self, 'wallet', None)
        wn = ("/" + w.diagnostic_name()) if w else ""
        return f'{dn}{wn}'

    @staticmethod
    def get_outpoint_longname(utxo) -> str:
        return f"{utxo['prevout_hash']}:{utxo['prevout_n']}"

    def get_utxo(self, name: str) -> Optional[dict]:
        return self.utxos_by_name.get(name)

    def clear_form(self):
        """Bring this form back to the initial state, clear text fields, etc"""
        self.tw_nft.itemChanged.disconnect(self.on_nft_item_changed)  # Need to disable this signal temporarily
        for tid in list(self.token_fungible_to_spend):
            self.token_fungible_to_spend[tid] = 0
        for tid in list(self.token_nfts_selected):
            self.token_nfts_selected[tid].clear()
        self.rebuild_nfts_to_send_treewidget()
        self.rebuild_tokens_to_send_treewidget()
        self.cb_max.setChecked(False)
        self.fee_slider.setValue(0)
        self.amount_e.clear()
        if not self.edit_mode:
            self.te_payto.clear()
        self.te_desc.clear()
        self.tw_nft.itemChanged.connect(self.on_nft_item_changed)
        self.on_ui_state_changed()

    def check_sanity(self) -> bool:
        sane = True
        ft_total = sum(amt for amt in self.token_fungible_to_spend.values())
        num_nfts = sum(len(s) for s in self.token_nfts_selected.values())
        if max(ft_total, 0) + num_nfts <= 0:
            # No tokens specified!
            sane = False
        elif not address.Address.is_valid(self.te_payto.toPlainText().strip()):
            # Bad address
            sane = False
        if sane and self.edit_mode:
            # Checks for edit mode only
            if any(c is None for c in self.nfts_to_edit.values()):
                # Bad NFT commitment specified
                sane = False
            else:
                # Ensure that at least one modified selection exists
                modct = 0
                for s in self.token_nfts_selected.values():
                    if modct:
                        break
                    for name in s:
                        if modct:
                            break
                        utxo = self.get_utxo(name)
                        td = utxo['token_data']
                        new_commitment = self.nfts_to_edit.get(name)
                        modct += new_commitment is not None and td.commitment != new_commitment
                if not modct:
                    # No modified selections exist, bail
                    sane = False
        return sane

    def _estimate_max_amount(self):
        spec = self.make_token_send_spec(dummy=True)
        try:
            tx = self.wallet.make_token_send_tx(self.parent.config, spec)
        except Exception as e:
            self.print_error("_estimate_max_amount:", repr(e))
            return None
        dust_regular = wallet.dust_threshold(self.wallet.network)
        dust_token = token.heuristic_dust_limit_for_token_bearing_output()
        # Consider all non-token non-dust utxos as potentially contributing to max_amount
        max_in = sum(x['value'] for x in spec.non_token_utxos.values() if x['value'] >= dust_regular)
        # Quirk: We don't choose token utxos for contributing to BCH amount unless the token was selected for
        # sending by the user in the UI. So only consider BCH amounts > 800 sats for tokens chosen for this tx
        # by the user's NFT/FT selections in the UI.
        max_in += sum(x['value'] - dust_token for x in tx.inputs() if x['token_data'] and x['value'] > dust_token)

        val_out_minus_change = 0
        for (_, addr, val), td in tx.outputs(tokens=True):
            if td or addr != spec.change_addr:
                val_out_minus_change += val
        bytes = tx.serialize_bytes(estimate_size=True)
        max_amount = max(0, max_in - val_out_minus_change - int(math.ceil(len(bytes)/1000 * spec.feerate)))
        return max_amount

    def on_ui_state_changed(self):
        if not self.fully_constructed:
            return
        sane = self.check_sanity()
        self.but_preview_tx.setEnabled(sane)

        # Manage amt color state
        amt_color = ColorScheme.DEFAULT
        if self.cb_max.isChecked():
            amt = self._estimate_max_amount()
            self.amount_e.setAmount(amt or 0)
        else:
            amt = self.amount_e.get_amount()
            if amt is not None:
                max_amt = self._estimate_max_amount()
                if max_amt is not None and amt > max_amt:
                    amt_color = ColorScheme.RED
        self.amount_e.setStyleSheet(amt_color.as_stylesheet())

        # Manage address color state
        addr_color = ColorScheme.DEFAULT
        msg = ""
        if sane:
            addr_str = self.te_payto.toPlainText().strip()
            if address.Address.is_token(addr_str):
                addr_color = ColorScheme.GREEN
            else:
                addr_color = ColorScheme.YELLOW
                msg = _("Not a CashToken-aware address")
        self.te_payto.setStyleSheet(addr_color.as_stylesheet())
        self.lbl_status_msg.setText(msg)
        self.lbl_status_msg.setStyleSheet(addr_color.as_stylesheet() if msg else ColorScheme.DEFAULT.as_stylesheet())

    def make_token_send_spec(self, dummy=False) -> wallet.TokenSendSpec:
        spec = wallet.TokenSendSpec()
        if dummy:
            spec.payto_addr = self.wallet.dummy_address()
        else:
            spec.payto_addr = address.Address.from_string(self.te_payto.text().strip())
        spec.change_addr = self.wallet.get_unused_address(for_change=True, frozen_ok=False)
        spec.feerate = self.fee_rate
        if dummy:
            spec.send_satoshis = wallet.dust_threshold(self.wallet.network)
        else:
            spec.send_satoshis = self.amount_e.get_amount() or 0
        spec.token_utxos = copy.deepcopy(self.utxos_by_name)
        spec.non_token_utxos = {self.get_outpoint_longname(x): x
                                for x in self.wallet.get_spendable_coins(None, self.parent.config)}
        spec.send_fungible_amounts = {tid: amt for tid, amt in self.token_fungible_to_spend.items()}
        spec.send_nfts = set()
        for tid, utxo_name_set in self.token_nfts_selected.items():
            if not self.edit_mode:
                spec.send_nfts |= utxo_name_set
            else:
                # In edit mode, only pick up NFTs that changed
                for utxo_name in utxo_name_set:
                    new_commitment = self.nfts_to_edit.get(utxo_name)
                    commitment = self.utxos_by_name[utxo_name]['token_data'].commitment
                    if new_commitment is not None and new_commitment != commitment:
                        spec.send_nfts.add(utxo_name)
                        spec.edit_nfts[utxo_name] = new_commitment

        # In edit mode, avoid splitting NFTs with amounts on them when editing them, by specifying that
        # the fungible amount should be "sent"
        if self.edit_mode:
            for utxo_name in spec.edit_nfts:
                utxo = spec.get_utxo(utxo_name)
                td = utxo['token_data']
                spec.send_fungible_amounts[td.id_hex] = td.amount + spec.send_fungible_amounts.get(td.id_hex, 0)

        # 'dummy' mode only: Try and fill in at least 1 nft or fungible amount
        if dummy and not spec.send_nfts and sum(spec.send_fungible_amounts.values()) <= 0:
            for name, utxo in spec.token_utxos.items():
                td = utxo['token_data']
                if td.has_nft():
                    spec.send_nfts.add(name)
                    break
                elif td.amount:
                    spec.send_fungible_amounts[td.id_hex] = td.amount
                    break

        return spec

    def on_preview_tx(self):
        # First, we must make sure that any amount line-edits have lost focus, so we can be 100% sure
        # "textEdited" signals propagate and what the user sees on-screen is what ends-up in the txn
        w = self.focusWidget()
        if w:
            w.clearFocus()
        # Check sanity just in case the above caused us to no longer be "sane"
        if not self.check_sanity():
            self.print_error("Spurious click of 'preview tx', returning early")
            return
        spec = self.make_token_send_spec()
        try:
            tx = self.wallet.make_token_send_tx(self.parent.config, spec)
            if tx:
                self.parent.show_transaction(tx, tx_desc=self.te_desc.toPlainText().strip() or None,
                                             broadcast_callback=self.broadcast_callback)
            else:
                self.show_error("Unimplemented")
        except wallet.NotEnoughFunds as e:
            self.show_error(str(e) or _("Not enough funds"))
        except wallet.ExcessiveFee as e:
            self.show_error(str(e) or _("Excessive fee"))
        except wallet.TokensBurnedError as e:
            self.show_error(str(e) or _("Internal Error: Transaction generation yielded a transaction in which"
                                        " some tokens are being burned;  refusing to proceed. Please report this"
                                        " situation to the developers."))
