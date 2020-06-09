#!/usr/bin/env python3
# Copyright (c) 2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test p2p blocksonly mode & block-relay-only connections."""

import time

from test_framework.blocktools import create_transaction
from test_framework.messages import msg_tx
from test_framework.mininode import P2PInterface, P2PTxInvStore
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal

class P2PBlocksOnly(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = False
        self.num_nodes = 1
        self.extra_args = [["-blocksonly"]]

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def run_test(self):
        self.blocksonly_mode_tests()
        self.blocks_relay_conn_tests()

    def blocksonly_mode_tests(self):
        self.log.info("Tests with node running in -blocksonly mode")
        assert_equal(self.nodes[0].getnetworkinfo()['localrelay'], False)

        self.nodes[0].add_p2p_connection(P2PInterface())
        tx, txid, tx_hex = self.check_p2p_tx_violation()

        self.log.info('Check that txs from rpc are not rejected and relayed to other peers')
        self.nodes[0].add_p2p_connection(P2PInterface())
        assert_equal(self.nodes[0].getpeerinfo()[0]['relaytxes'], True)

        assert_equal(self.nodes[0].testmempoolaccept([tx_hex])[0]['allowed'], True)
        with self.nodes[0].assert_debug_log(['received getdata for: wtx {} peer=1'.format(txid)]):
            self.nodes[0].sendrawtransaction(tx_hex)
            self.nodes[0].p2p.wait_for_tx(txid)
            assert_equal(self.nodes[0].getmempoolinfo()['size'], 1)

        self.log.info("Restarting node 0 with forcerelay permission and blocksonly")
        self.restart_node(0, ["-persistmempool=0", "-whitelist=127.0.0.1", "-whitelistforcerelay", "-blocksonly"])
        assert_equal(self.nodes[0].getrawmempool(), [])
        first_peer = self.nodes[0].add_p2p_connection(P2PInterface())
        second_peer = self.nodes[0].add_p2p_connection(P2PInterface())
        peer_1_info = self.nodes[0].getpeerinfo()[0]
        assert_equal(peer_1_info['whitelisted'], True)
        assert_equal(peer_1_info['permissions'], ['noban', 'forcerelay', 'relay', 'mempool', 'download'])
        peer_2_info = self.nodes[0].getpeerinfo()[1]
        assert_equal(peer_2_info['whitelisted'], True)
        assert_equal(peer_2_info['permissions'], ['noban', 'forcerelay', 'relay', 'mempool', 'download'])
        assert_equal(self.nodes[0].testmempoolaccept([tx_hex])[0]['allowed'], True)

        self.log.info('Check that the tx from forcerelay first_peer is relayed to others (ie.second_peer)')
        with self.nodes[0].assert_debug_log(["received getdata"]):
            first_peer.send_message(msg_tx(tx))
            self.log.info('Check that the forcerelay peer is still connected after sending the transaction')
            assert_equal(first_peer.is_connected, True)
            second_peer.wait_for_tx(txid)
            assert_equal(self.nodes[0].getmempoolinfo()['size'], 1)
        self.log.info("Forcerelay peer's transaction is accepted and relayed")

        self.nodes[0].disconnect_p2ps()
        self.nodes[0].generate(1)

    def blocks_relay_conn_tests(self):
        self.log.info('Tests with node in normal mode with block-relay-only connections')
        self.restart_node(0, ["-noblocksonly"])  # disables blocks only mode
        assert_equal(self.nodes[0].getnetworkinfo()['localrelay'], True)

        # Ensure we disconnect if a block-relay-only connection sends us a transaction
        self.nodes[0].add_outbound_p2p_connection(P2PInterface(), connection_type="blockrelay")
        assert_equal(self.nodes[0].getpeerinfo()[0]['relaytxes'], False)
        _, txid, tx_hex = self.check_p2p_tx_violation(index=2)

        self.log.info("Check that txs from RPC are not sent to blockrelay connection")
        conn = self.nodes[0].add_outbound_p2p_connection(P2PTxInvStore(), connection_type="blockrelay")

        # bump time forward to ensure nNextInvSend timer pops
        self.nodes[0].setmocktime(int(time.time()) + 5)

        assert_equal(self.nodes[0].testmempoolaccept([tx_hex])[0]['allowed'], True)
        self.nodes[0].sendrawtransaction(tx_hex)
        conn.sync_with_ping()
        assert(int(txid, 16) not in conn.get_invs())

    def check_p2p_tx_violation(self, index=1):
        self.log.info('Check that txs from P2P are rejected and result in disconnect')
        input_txid = self.nodes[0].getblock(self.nodes[0].getblockhash(index), 2)['tx'][0]['txid']
        tx = create_transaction(self.nodes[0], input_txid, self.nodes[0].getnewaddress(), amount=50 - 0.001)
        txid = tx.rehash()
        tx_hex = tx.serialize().hex()

        with self.nodes[0].assert_debug_log(['transaction sent in violation of protocol peer=0']):
            self.nodes[0].p2p.send_message(msg_tx(tx))
            self.nodes[0].p2p.wait_for_disconnect()
            assert_equal(self.nodes[0].getmempoolinfo()['size'], 0)

        # Remove the disconnected peer
        del self.nodes[0].p2ps[0]

        return tx, txid, tx_hex


if __name__ == '__main__':
    P2PBlocksOnly().main()
