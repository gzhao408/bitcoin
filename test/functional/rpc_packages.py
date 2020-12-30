#!/usr/bin/env python3
# Copyright (c) 2021 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test packages with raw transaction RPCs."""

from decimal import Decimal
from io import BytesIO

from test_framework.address import ADDRESS_BCRT1_P2WSH_OP_TRUE
from test_framework.test_framework import BitcoinTestFramework
from test_framework.messages import CTransaction
from test_framework.util import (
    assert_equal,
    hex_str_to_bytes,
)

class RPCPackagesTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        self.extra_args = [[]]
        self.setup_clean_chain = True

    def run_test(self):
        self.log.info("Generate blocks to create UTXOs")
        node = self.nodes[0]
        self.privkeys = [node.get_deterministic_priv_key().key]
        self.address = node.get_deterministic_priv_key().address
        node.generatetoaddress(200, self.address)
        self.privkeys=[self.nodes[0].get_deterministic_priv_key().key]

        self.test_chain()
        self.test_conflicting()
        self.test_multiple_independent()

    def chain_transaction(self, parent_txid, value, parent_scriptPubKey=None):
        """Build a transaction that spends parent_txid:vout. Return tuple (transaction id, raw hex)."""
        node = self.nodes[0]
        inputs = [{"txid" : parent_txid, "vout" : 0}]
        outputs = {self.address : value}
        rawtx = node.createrawtransaction(inputs, outputs)
        prevtxs = [{
            "txid": parent_txid,
            "vout": 0,
            "scriptPubKey": parent_scriptPubKey,
            "amount": value + Decimal("0.0001"),
        }] if parent_scriptPubKey else None
        signedtx = node.signrawtransactionwithkey(hexstring=rawtx, privkeys=self.privkeys, prevtxs=prevtxs)
        tx = CTransaction()
        assert signedtx["complete"]
        tx.deserialize(BytesIO(hex_str_to_bytes(signedtx["hex"])))
        return (tx.rehash(), signedtx["hex"], tx.vout[0].scriptPubKey.hex())

    def test_chain(self):
        node = self.nodes[0]
        first_coin = node.listunspent(query_options={"minimumAmount": 50}).pop()

        self.log.info("Create a chain of 3 transactions")
        scriptPubKey = None
        txid = first_coin["txid"]
        chain = []
        value = Decimal("50.0")

        for _ in range(3):
            value -= Decimal("0.0001") # Deduct reasonable fee
            (txid, txhex, scriptPubKey) = self.chain_transaction(txid, value, scriptPubKey)
            chain.append(txhex)

        self.log.info("Testmempoolaccept with entire package")
        testres_multiple = node.testmempoolaccept(rawtxs=chain)

        testres_single = []
        self.log.info("Test accept and then submit each one individually, which should be identical to package testaccept")
        for rawtx in chain:
            tx = CTransaction()
            tx.deserialize(BytesIO(hex_str_to_bytes(rawtx)))
            testres = node.testmempoolaccept([rawtx])
            testres_single.append(testres)
            # Submit the transaction now so its child should have no problem validating
            node.sendrawtransaction(rawtx)

        for i in range(3):
            assert_equal(testres_single[i][0], testres_multiple[i])


    def test_conflicting(self):
        node = self.nodes[0]
        self.mempool_size = 0
        prevtx = node.listunspent(query_options={"minimumAmount": 50}).pop()
        inputs = [{"txid" : prevtx["txid"], "vout" : 0}]
        output1 = {node.get_deterministic_priv_key().address: 50 - 0.00125}
        output2 = {ADDRESS_BCRT1_P2WSH_OP_TRUE: 50 - 0.00125}

        # tx1 and tx2 share the same inputs
        rawtx1 = node.createrawtransaction(inputs, output1)
        rawtx2 = node.createrawtransaction(inputs, output2)
        signedtx1 = node.signrawtransactionwithkey(hexstring=rawtx1, privkeys=self.privkeys)
        signedtx2 = node.signrawtransactionwithkey(hexstring=rawtx2, privkeys=self.privkeys)
        tx1 = CTransaction()
        tx1.deserialize(BytesIO(hex_str_to_bytes(signedtx1["hex"])))
        tx2 = CTransaction()
        tx2.deserialize(BytesIO(hex_str_to_bytes(signedtx2["hex"])))
        assert signedtx1["complete"]
        assert signedtx2["complete"]

        # Ensure tx1 and tx2 are valid by themselves
        assert node.testmempoolaccept([signedtx1["hex"]])[0]["allowed"]
        assert node.testmempoolaccept([signedtx2["hex"]])[0]["allowed"]

        self.log.info("Test duplicate transactions in the same package")
        testres = node.testmempoolaccept([signedtx1["hex"], signedtx1["hex"]])
        assert_equal(testres, [{"txid": tx1.rehash(), "allowed": False, "reject-reason": "txn-already-known"}])

        self.log.info("Test conflicting transactions in the same package")
        testres = node.testmempoolaccept([signedtx1["hex"], signedtx2["hex"]])
        assert_equal(testres, [{"txid": tx2.rehash(), "allowed": False, "reject-reason": "missing-inputs"}])

    def test_multiple_independent(self):
        self.log.info("Test multiple independent transactions in a package")
        node = self.nodes[0]
        coins = node.listunspent(query_options={"minimumAmount": 50})
        independent_txns = []
        testres_single = []
        for _ in range(3):
            coin = coins.pop()
            rawtx = node.createrawtransaction([{"txid" : coin["txid"], "vout" : 0}],
                {self.address : coin["amount"] - Decimal("0.0001")})
            signedtx = node.signrawtransactionwithkey(hexstring=rawtx, privkeys=self.privkeys)
            assert signedtx["complete"]
            testres = node.testmempoolaccept([signedtx["hex"]])
            assert testres[0]["allowed"]
            testres_single.append(testres)
            independent_txns.append(signedtx["hex"])
        testres_multiple = node.testmempoolaccept(rawtxs=independent_txns)

        # Testing a package of independent txns should be identical to testing them individually
        for i in range(3):
            assert_equal(testres_single[i][0], testres_multiple[i])

        self.log.info("Test valid package with garbage inserted")
        garbage_tx = node.createrawtransaction([{"txid" : "00" * 32, "vout" : 5}], {self.address : 1})
        testres_bad = node.testmempoolaccept(independent_txns + [garbage_tx])
        assert_equal(len(testres_bad), 1)


if __name__ == "__main__":
    RPCPackagesTest().main()
