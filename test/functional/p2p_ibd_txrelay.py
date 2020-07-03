#!/usr/bin/env python3
# Copyright (c) 2020 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test fee filters during and after IBD."""

from decimal import Decimal

from test_framework.messages import COIN
from test_framework.mininode import P2PTxInvStore
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, connect_nodes

MAX_FEE_FILTER = Decimal(9170997) / COIN
NORMAL_FEE_FILTER = Decimal(100) / COIN
MAX_TIP_AGE = 24 * 60 * 60


class P2PIBDTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = False
        self.num_nodes = 2
        self.extra_args = [["-minrelaytxfee={}".format(NORMAL_FEE_FILTER)]] * self.num_nodes

    def run_test(self):
        # Generate some blocks so that node0 is funded
        self.nodes[0].generate(nblocks=101)
        self.sync_blocks(self.nodes)

        # Force node1 into IBD by restarting and fast-forwarding time.
        # Fast-forwarding the time by > 24 hours causes node1 to consider its tip too old
        # See the tip age check in IsInitialBlockDownload().
        self.restart_node(1)
        tip_time = self.nodes[1].getblockheader(self.nodes[1].getbestblockhash())['time']
        self.nodes[1].setmocktime(tip_time + MAX_TIP_AGE + 1)
        connect_nodes(self.nodes[0], 1)
        connect_nodes(self.nodes[1], 0)

        self.log.info("Check that nodes set minfilter to MAX_MONEY and do not receive tx invs while still in IBD")
        assert self.nodes[1].getblockchaininfo()['initialblockdownload']
        assert_equal(self.nodes[0].getpeerinfo()[0]['minfeefilter'], MAX_FEE_FILTER)

        # We can only check tx invs through mininodes, as the info isn't reported through bitcoind interfaces
        # Peer0 knows what invs are being sent by node0, and peer1 knows what txns are in node1's mempool
        # The best we can do is confirm that node0 is indeed sending out invs here
        peer0 = self.nodes[0].add_p2p_connection(P2PTxInvStore())
        peer1 = self.nodes[1].add_p2p_connection(P2PTxInvStore())
        tx_ibd = self.nodes[0].sendtoaddress(self.nodes[0].getnewaddress(), 0.1)
        peer0.sync_with_ping()
        peer0.wait_for_tx(tx_ibd)
        # Nothing needs to be synced, but ensure that non-receipt is not due to p2p delays
        peer1.sync_with_ping()
        assert tx_ibd not in peer1.get_invs()

        # Come out of IBD by generating a block
        # The block includes tx_ibd, so node1 will never have it in mempool - we need to create a new tx
        self.nodes[0].generate(1)
        self.sync_all()

        self.log.info("Check that nodes reset minfilter and receive tx invs after coming out of IBD")
        assert not self.nodes[1].getblockchaininfo()['initialblockdownload']
        assert_equal(self.nodes[0].getpeerinfo()[0]['minfeefilter'], NORMAL_FEE_FILTER)

        txid = self.nodes[0].sendtoaddress(self.nodes[0].getnewaddress(), 0.1)
        self.sync_all()
        peer1.sync_with_ping()
        peer1.wait_for_tx(txid)

if __name__ == '__main__':
    P2PIBDTest().main()
