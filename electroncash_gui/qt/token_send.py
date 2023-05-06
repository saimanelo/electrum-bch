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

from collections import defaultdict
from enum import IntEnum
from typing import DefaultDict, Dict, List, Optional, Set

from PyQt5 import QtCore, QtGui, QtWidgets

from electroncash.i18n import _
from electroncash import address, networks, token, util, wallet

from .amountedit import AmountEdit, BTCAmountEdit
from .fee_slider import FeeSlider
from .main_window import ElectrumWindow
from .qrtextedit import ScanQRTextEdit
from .util import EnterButton, HelpLabel, OnDestroyedMixin, PrintError, WindowModalDialog


class SendTokenForm(WindowModalDialog, PrintError, OnDestroyedMixin):

    class ColsTok(IntEnum):
        token_id = 0
        nfts = 1
        amount = 2
        amount_send = 3

    class ColsNFT(IntEnum):
        attqch = 0
        token_id = 1
        commitment = 2
        flags = 3

    class DataRoles(IntEnum):
        token_id = QtCore.Qt.UserRole
        output_point = QtCore.Qt.UserRole + 1
        receives_nft_count_updates = QtCore.Qt.UserRole + 2
        receives_nft_flag_updates = QtCore.Qt.UserRole + 3

    headers_tok = [_("Token ID"), _("NFTs to Send"), _("Fungible Amount"), _("Fungible Amount to Send")]
    headers_nft = [_("Attach"), _("Token ID"), _("Commitment"), _("Flags")]

    def __init__(self, parent: ElectrumWindow, token_utxos: List[dict]):
        assert isinstance(parent, ElectrumWindow)
        title = _("Send Tokens") + " - " + parent.wallet.basename()
        super().__init__(parent=parent, title=title)
        PrintError.__init__(self)
        OnDestroyedMixin.__init__(self)
        util.finalization_print_error(self)
        self.setWindowIcon(QtGui.QIcon(":icons/tab_send.png"))
        self.parent = parent
        self.wallet: wallet.Abstract_Wallet = self.parent.wallet
        self.utxos_by_name: Dict[str, dict] = dict()
        self.token_utxos: DefaultDict[str, List[str]] = defaultdict(list)  # tokenid -> unique sorted list of utxonames
        self.token_nfts: DefaultDict[str, List[str]] = defaultdict(list)
        self.token_fungible_only: DefaultDict[str, List[str]] = defaultdict(list)
        self.token_fungible_totals: DefaultDict[str, int] = defaultdict(int)  # tokenid -> fungible total
        self.token_nfts_selected: DefaultDict[str, Set[str]] = defaultdict(set)  # tokenid -> set of selected utxonames
        self.token_fungible_to_spend: DefaultDict[str, int] = defaultdict(int)  # tokenid -> amount

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
                self.token_nfts_selected[tid].clear()  # Start out with nothing selected
            else:
                assert td.has_amount()
                self.token_fungible_only[tid].append(name)
            self.token_fungible_totals[tid] += td.amount

        # Build UI
        vbox = QtWidgets.QVBoxLayout(self)

        gb = QtWidgets.QGroupBox(_("Tokens to Send"))
        vbox_gb = QtWidgets.QVBoxLayout(gb)

        self.tw_tok = tw = QtWidgets.QTreeWidget()
        tw.setAlternatingRowColors(True)
        tw.setSortingEnabled(False)
        tw.setTextElideMode(QtCore.Qt.ElideMiddle)
        tw.setHeaderLabels(self.headers_tok)
        tw.header().setSectionResizeMode(self.ColsTok.amount_send, QtWidgets.QHeaderView.Stretch)
        vbox_gb.addWidget(tw)

        self.gb_nft = gb_nft = QtWidgets.QGroupBox(_("NFTs to Send"))
        gb_nft_vbox = QtWidgets.QVBoxLayout(gb_nft)

        self.tw_nft = tw = QtWidgets.QTreeWidget()
        tw.setAlternatingRowColors(True)
        tw.setSortingEnabled(False)
        tw.setTextElideMode(QtCore.Qt.ElideMiddle)
        tw.setHeaderLabels(self.headers_nft)
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
        te.textChanged.connect(self.enable_disable_preview_tx_button)

        vbox.addWidget(gb)
        self._adjust_te_payto_size()

        # Additional BCH to Send
        self._setup_additional_bch_gbox(vbox)

        # Bottom buttons
        hbox = QtWidgets.QHBoxLayout()
        but_clear = QtWidgets.QPushButton(_("Clear"))
        but_clear.clicked.connect(self.clear_form)
        hbox.addWidget(but_clear)
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

        self.enable_disable_preview_tx_button()

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

    def _setup_additional_bch_gbox(self, vbox: QtWidgets.QVBoxLayout):
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
        self.amount_e.setFixedWidth(140)
        grid.addWidget(self.amount_e, row, col)
        l.setBuddy(self.amount_e)
        col += 1

        self.fiat_send_e = AmountEdit(self.parent.fx.get_currency if self.parent.fx else '')
        if not self.parent.fx or not self.parent.fx.is_enabled():
            self.fiat_send_e.setVisible(False)
        grid.addWidget(self.fiat_send_e, row, col)
        grid.setAlignment(self.fiat_send_e, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.amount_e.frozen.connect(lambda: self.fiat_send_e.setFrozen(self.amount_e.isReadOnly()))
        grid.setAlignment(self.amount_e, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        col += 1

        def spend_max():
            self.max_button.setChecked(True)
            # self.do_update_fee()
        self.max_button = EnterButton(_("&Max"), spend_max)
        self.max_button.setCheckable(True)
        grid.addWidget(self.max_button, row, col)
        col += 1

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

        def fee_cb(dyn, pos, fee_rate):
            pass

        self.fee_slider = FeeSlider(self.parent, self.parent.config, fee_cb)
        self.fee_slider.setFixedWidth(140)
        grid.addWidget(self.fee_slider, row, col)
        fee_e_label.setBuddy(self.fee_slider)

        vbox.addWidget(gb)

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
        if tid and name and column == 0:
            b = item.checkState(column) == QtCore.Qt.Checked
            if b:
                self.token_nfts_selected[tid].add(name)
            else:
                self.token_nfts_selected[tid].discard(name)
            self.update_tokens_to_send_nft_count(tid)
            # Note: disabled for now since I found this distracting to see in the UI
            # self.update_tokens_to_send_nft_flags(tid)
            self.enable_disable_preview_tx_button()

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
        tws = [(self.tw_tok, self.ColsTok.nfts), (self.tw_nft, self.ColsNFT.attqch)]
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
            item = QtWidgets.QTreeWidgetItem([tid, "", str(amt), ""])
            item.setToolTip(self.ColsTok.token_id, item.text(self.ColsTok.token_id))
            item.setToolTip(self.ColsTok.amount, item.text(self.ColsTok.amount))
            item.setData(0, self.DataRoles.token_id, tid)
            item.setData(0, self.DataRoles.receives_nft_count_updates, True)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
            tw.addTopLevelItem(item)
            # Update NFTs M/N column (must be done after add)
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
                self.enable_disable_preview_tx_button()
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

    @staticmethod
    def get_nft_flag(td: token.OutputData) -> Optional[str]:
        if td.is_minting_nft():
            return _('Minting')
        elif td.is_mutable_nft():
            return _('Mutable')
        elif td.is_immutable_nft():
            return _('Immutable')

    def rebuild_nfts_to_send_treewidget(self):
        tw = self.tw_nft
        tw.clear()

        def add_leaf_item(parent: QtWidgets.QTreeWidgetItem, tid, name) -> QtWidgets.QTreeWidgetItem:
            utxo = self.get_utxo(name)
            td = utxo['token_data']
            assert isinstance(td, token.OutputData)
            item = QtWidgets.QTreeWidgetItem(["", tid, td.commitment.hex(), self.get_nft_flag(td)])
            item.setToolTip(self.ColsNFT.attqch, _("Check to send this NFT"))
            item.setToolTip(self.ColsNFT.token_id, tid)
            item.setToolTip(self.ColsNFT.commitment, td.commitment.hex()
                            if td.commitment else _("zero-length commitment"))
            item.setFlags((item.flags() | QtCore.Qt.ItemIsUserCheckable) & ~QtCore.Qt.ItemIsSelectable)
            item.setData(0, self.DataRoles.token_id, tid)
            item.setData(0, self.DataRoles.output_point, name)
            parent.addChild(item)
            item.setCheckState(0, QtCore.Qt.Checked)  # Need to call this at least once to make checkbox appear
            if name not in self.token_nfts_selected.get(tid, set()):
                item.setCheckState(0, QtCore.Qt.Unchecked)
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

        # Re-populate flags column for parent items
        # Note: disabled for now as I found this distracting in the UI
        # for tid in self.token_nfts.keys():
        #     self.update_tokens_to_send_nft_flags(tid)

        # if we have no NFTs, hide this widget completely
        self.gb_nft.setHidden(not self.have_nfts())

    def diagnostic_name(self):
        dn = super().diagnostic_name()
        w = getattr(self, 'wallet', None)
        wn = ("/" + w.diagnostic_name()) if w else ""
        return f'{dn}{wn}'

    @classmethod
    def get_outpoint_shortname(cls, utxo) -> str:
        return cls.elide(utxo['prevout_hash'], 12) + ':' + str(utxo['prevout_n'])

    @staticmethod
    def get_outpoint_longname(utxo) -> str:
        return f"{utxo['prevout_hash']}:{utxo['prevout_n']}"

    def get_utxo(self, name: str) -> Optional[dict]:
        return self.utxos_by_name.get(name)

    @staticmethod
    def elide(s: str, elide_threshold=32) -> str:
        if len(s) > elide_threshold:
            n = max(elide_threshold // 2, 0)
            return s[:n] + '…' + s[-n:]
        return s

    def clear_form(self):
        """Bring this form back to the initial state, clear text fields, etc"""
        self.tw_nft.itemChanged.disconnect(self.on_nft_item_changed)  # Need to disable this signal temporarily
        for tid in list(self.token_fungible_to_spend):
            self.token_fungible_to_spend[tid] = 0
        for tid in list(self.token_nfts_selected):
            self.token_nfts_selected[tid].clear()
        self.rebuild_nfts_to_send_treewidget()
        self.rebuild_tokens_to_send_treewidget()
        self.max_button.setChecked(False)
        self.fee_slider.setValue(self.fee_slider.minimum())
        self.amount_e.clear()
        self.fiat_send_e.clear()
        self.te_payto.clear()
        self.tw_nft.itemChanged.connect(self.on_nft_item_changed)
        self.enable_disable_preview_tx_button()

    def check_sanity(self) -> bool:
        sane = True
        ft_total = sum(amt for amt in self.token_fungible_to_spend.values())
        num_nfts = sum(len(s) for s in self.token_nfts_selected.values())
        if max(ft_total, 0) + num_nfts <= 0:
            # No tokens specified!
            sane = False
        elif not address.Address.is_valid(self.te_payto.text().strip()):
            # Bad address
            sane = False
        return sane

    def enable_disable_preview_tx_button(self):
        self.but_preview_tx.setEnabled(self.check_sanity())

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
        self.show_error("Unimplemented")
