#!/usr/bin/env python3
# Copyright (c) 2019-2020 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""
Test how expensive a transaction can be
"""
from decimal import Decimal
from io import BytesIO
import time

from test_framework.messages import (
    CTransaction,
    msg_tx,
)
from test_framework.p2p import (
    P2PInterface,
)

from test_framework.test_framework import BitcoinTestFramework

from test_framework.util import (
    hex_str_to_bytes,
)

# Constants from consensus and policy
MAX_BLOCK_SIGOPS_COST = 80000
MAX_STANDARD_TX_SIGOPS_COST = int(MAX_BLOCK_SIGOPS_COST/5)
MAX_STANDARD_TX_WEIGHT = 400000

# Constants from net_processing
MAX_GETDATA_IN_FLIGHT = 100

# Test params
# 679 sigops and 99.8KvB (maximum is 100KvB)
MANY_SIGNATURES = 679
NUM_TRANSACTIONS = 5
COINS_NEEDED = MANY_SIGNATURES * NUM_TRANSACTIONS + 100

class TestTransactionDos(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1

    def create_dossy_transaction(self):
        """Create a transaction with a lot of sigops that takes a long time to reject.
        The transaction should be consensus-valid so that the sending peer is not disconnected.
        Add a policy violation to the last input so that the transaction fails at the very end,
        after all of its signatures have been verified.
        """
        testnode = self.nodes[0]
        inputs = []
        amount = 0
        for i in range(MANY_SIGNATURES):
            coin = self.coins.pop()
            inputs.append({"txid" : coin["txid"], "vout": 0})
            amount += coin["amount"]
        amount -= Decimal("0.01")
        outputs = {self.address : amount}
        rawtx = testnode.createrawtransaction(inputs, outputs)
        prevtxs = None
        signedtx = testnode.signrawtransactionwithkey(hexstring=rawtx, privkeys=self.privkeys, prevtxs=prevtxs)
        tx = CTransaction()
        assert signedtx["complete"]
        tx.deserialize(BytesIO(hex_str_to_bytes(signedtx["hex"])))
        assert tx.get_vsize() < 400000//4
        # the transaction should pass here
        # commented out so the node doesn't see the trransaction ahead of time
        # testres = testnode.testmempoolaccept([signedtx["hex"]])
        # if not testres[0]["allowed"]:
        #     print(testres)
        # assert testres[0]["allowed"]

        # Add a OP_1 at the end to fail cleanstack
        tx.vin[-1].scriptSig += b'\x01'
        return tx


    def run_test(self):
        testnode = self.nodes[0]
        self.dossy_transactions = []
        self.log.info("Generate blocks to create UTXOs")
        self.privkeys = [testnode.get_deterministic_priv_key().key]
        self.address = testnode.get_deterministic_priv_key().address
        self.coins = []
        # The last 100 coinbase transactions are premature
        for b in testnode.generatetoaddress(COINS_NEEDED, self.address)[:-100]:
            coinbase = testnode.getblock(blockhash=b, verbosity=2)["tx"][0]
            self.coins.append({
                "txid": coinbase["txid"],
                "amount": coinbase["vout"][0]["value"],
            })
        self.log.info("Create transactions that take a long time to validate")
        self.dossy_transactions = []
        for _ in range(NUM_TRANSACTIONS):
            tx = self.create_dossy_transaction()
            self.dossy_transactions.append(tx)
            # test_res = testnode.testmempoolaccept([tx.serialize().hex()])
            # assert_equal(test_res[0]['reject-reason'], 'scriptsig-not-pushonly')

        victimnode = self.nodes[0]
        dos_peer = victimnode.add_p2p_connection(P2PInterface())
        # send tx unsolicited
        start = time.time()
        with victimnode.assert_debug_log(expected_msgs=["was not accepted: scriptsig-not-pushonly"], unexpected_msgs=["script cache hit"]):
            for tx in self.dossy_transactions:
                dos_peer.send_message(msg_tx(tx))
        end = time.time()
        assert dos_peer.is_connected
        print(end - start)


if __name__ == '__main__':
    TestTransactionDos().main()
