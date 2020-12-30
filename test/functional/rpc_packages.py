#!/usr/bin/env python3
# Copyright (c) 2021 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""RPCs that handle raw transaction packages."""

from decimal import Decimal
from io import BytesIO

from test_framework.address import ADDRESS_BCRT1_P2WSH_OP_TRUE
from test_framework.test_framework import BitcoinTestFramework
from test_framework.messages import (
    BIP125_SEQUENCE_NUMBER,
    COIN,
    CTransaction,
)
from test_framework.util import (
    assert_equal,
    hex_str_to_bytes,
)

class RPCPackagesTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        self.setup_clean_chain = True

    def run_test(self):
        self.log.info("Generate blocks to create UTXOs")
        node = self.nodes[0]
        self.privkeys = [node.get_deterministic_priv_key().key]
        self.address = node.get_deterministic_priv_key().address
        self.coins = []
        # The last 100 coinbase transactions are premature
        for b in node.generatetoaddress(120, self.address)[:20]:
            coinbase = node.getblock(blockhash=b, verbosity=2)["tx"][0]
            self.coins.append({
                "txid": coinbase["txid"],
                "amount": coinbase["vout"][0]["value"],
                "scriptPubKey": coinbase["vout"][0]["scriptPubKey"],
            })

        # Create some transactions that can be reused throughout the test. Never submit these to mempool.
        self.independent_txns_hex = []
        self.independent_txns_testres = []
        for _ in range(3):
            coin = self.coins.pop()
            rawtx = node.createrawtransaction([{"txid" : coin["txid"], "vout" : 0}],
                {self.address : coin["amount"] - Decimal("0.0001")})
            signedtx = node.signrawtransactionwithkey(hexstring=rawtx, privkeys=self.privkeys)
            assert signedtx["complete"]
            testres = node.testmempoolaccept([signedtx["hex"]])
            assert testres[0]["allowed"]
            self.independent_txns_hex.append(signedtx["hex"])
            # testmempoolaccept returns a list of length one, avoid creating a 2D list
            self.independent_txns_testres.append(testres[0])

        self.test_independent()
        self.test_chain()
        self.test_conflicting()
        self.test_rbf()

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

    def test_independent(self):
        self.log.info("Test multiple independent transactions in a package")
        node = self.nodes[0]
        assert_equal(self.independent_txns_testres, node.testmempoolaccept(rawtxs=self.independent_txns_hex))

        self.log.info("Test a valid package with garbage inserted")
        garbage_tx = node.createrawtransaction([{"txid": "00" * 32, "vout": 5}], {self.address: 1})
        tx = CTransaction()
        tx.deserialize(BytesIO(hex_str_to_bytes(garbage_tx)))
        testres_bad = node.testmempoolaccept(self.independent_txns_hex + [garbage_tx])
        assert_equal(testres_bad, [{"txid": tx.rehash(), "wtxid": tx.getwtxid(), "allowed": False, "reject-reason": "missing-inputs"}])

    def test_chain(self):
        node = self.nodes[0]
        first_coin = self.coins.pop()

        self.log.info("Create a chain of three transactions")
        scriptPubKey = None
        txid = first_coin["txid"]
        chain = []
        value = first_coin["amount"]

        for _ in range(3):
            value -= Decimal("0.0001") # Deduct reasonable fee
            (txid, txhex, scriptPubKey) = self.chain_transaction(txid, value, scriptPubKey)
            chain.append(txhex)

        self.log.info("Testmempoolaccept with entire package")
        testres_multiple = node.testmempoolaccept(rawtxs=chain)

        testres_single = []
        self.log.info("Test accept and then submit each one individually, which should be identical to package testaccept")
        for rawtx in chain:
            testres = node.testmempoolaccept([rawtx])
            testres_single.append(testres[0])
            # Submit the transaction now so its child should have no problem validating
            node.sendrawtransaction(rawtx)
        assert_equal(testres_single, testres_multiple)

        # Clean up by clearing the mempool
        node.generate(1)

    def test_conflicting(self):
        node = self.nodes[0]
        prevtx = self.coins.pop()
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
        assert_equal(testres, [{"txid": tx1.rehash(), "wtxid": tx1.getwtxid(), "allowed": False, "reject-reason": "conflict-in-package"}])

        self.log.info("Test conflicting transactions in the same package")
        testres = node.testmempoolaccept([signedtx1["hex"], signedtx2["hex"]])
        assert_equal(testres, [{"txid": tx2.rehash(), "wtxid": tx2.getwtxid(), "allowed": False, "reject-reason": "conflict-in-package"}])

    def test_rbf(self):
        node = self.nodes[0]
        coin = self.coins.pop()
        inputs = [{"txid" : coin["txid"], "vout" : 0, "sequence": BIP125_SEQUENCE_NUMBER}]
        fee = 0.00125
        output = {node.get_deterministic_priv_key().address: 50 - fee}
        raw_replaceable_tx = node.createrawtransaction(inputs, output)
        signed_replaceable_tx = node.signrawtransactionwithkey(hexstring=raw_replaceable_tx, privkeys=self.privkeys)
        testres_replaceable = node.testmempoolaccept([signed_replaceable_tx["hex"]])
        assert testres_replaceable[0]["allowed"]

        # Replacement transaction is identical except has double the fee
        replacement_tx = CTransaction()
        replacement_tx.deserialize(BytesIO(hex_str_to_bytes(signed_replaceable_tx["hex"])))
        replacement_tx.vout[0].nValue -= int(fee * COIN)  # Doubled fee
        signed_replacement_tx = node.signrawtransactionwithkey(replacement_tx.serialize().hex(), self.privkeys)
        replacement_tx.deserialize(BytesIO(hex_str_to_bytes(signed_replacement_tx["hex"])))

        self.log.info("Test that transactions within a package cannot replace each other")
        testres_rbf_conflicting = node.testmempoolaccept([signed_replaceable_tx["hex"], signed_replacement_tx["hex"]])
        assert_equal(testres_rbf_conflicting, [{"txid": replacement_tx.rehash(), "wtxid": replacement_tx.getwtxid(), "allowed": False, "reject-reason": "conflict-in-package"}])

        self.log.info("Test a package with a transaction replacing a mempool transaction")
        node.sendrawtransaction(signed_replaceable_tx["hex"])
        testres_rbf = node.testmempoolaccept(self.independent_txns_hex + [signed_replacement_tx["hex"]])
        testres_replacement = testres_replaceable[0]
        testres_replacement["txid"] = replacement_tx.rehash()
        testres_replacement["wtxid"] = replacement_tx.getwtxid()
        testres_replacement["fees"]["base"] = Decimal(str(2 * fee))
        assert_equal(testres_rbf, self.independent_txns_testres + [testres_replacement])


if __name__ == "__main__":
    RPCPackagesTest().main()
