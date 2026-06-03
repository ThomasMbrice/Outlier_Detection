"""Polygon JSON-RPC client for on-chain CTF Exchange event logs."""

from __future__ import annotations

from typing import Any, Dict, Generator, List

from web3 import Web3

from ..config import IngestionConfig, MigrationConfig

# Minimal ABI fragments — only the events we decode
_ORDER_FILLED_ABI_V1 = {
    "name": "OrderFilled",
    "type": "event",
    "inputs": [
        {"name": "orderHash", "type": "bytes32", "indexed": True},
        {"name": "maker", "type": "address", "indexed": True},
        {"name": "taker", "type": "address", "indexed": True},
        {"name": "makerAssetId", "type": "uint256", "indexed": False},
        {"name": "takerAssetId", "type": "uint256", "indexed": False},
        {"name": "makerAmountFilled", "type": "uint256", "indexed": False},
        {"name": "takerAmountFilled", "type": "uint256", "indexed": False},
    ],
}

_ORDER_FILLED_ABI_V2 = {
    "name": "OrderFilled",
    "type": "event",
    "inputs": [
        {"name": "orderHash", "type": "bytes32", "indexed": True},
        {"name": "maker", "type": "address", "indexed": True},
        {"name": "taker", "type": "address", "indexed": True},
        {"name": "makerAssetId", "type": "uint256", "indexed": False},
        {"name": "takerAssetId", "type": "uint256", "indexed": False},
        {"name": "makerAmountFilled", "type": "uint256", "indexed": False},
        {"name": "takerAmountFilled", "type": "uint256", "indexed": False},
        {"name": "fee", "type": "uint256", "indexed": False},
    ],
}


class PolygonRPCClient:
    BLOCK_CHUNK = 2_000  # logs per eth_getLogs call

    def __init__(self, cfg: IngestionConfig, mig: MigrationConfig) -> None:
        self._w3 = Web3(Web3.HTTPProvider(cfg.polygon_rpc))
        self._v1_addresses = [Web3.to_checksum_address(a) for a in mig.v1_exchange_addresses]
        self._v2_addresses = [Web3.to_checksum_address(a) for a in mig.v2_exchange_addresses]

        # Build contract objects for event decoding
        self._v1_contracts = [
            self._w3.eth.contract(address=a, abi=[_ORDER_FILLED_ABI_V1])
            for a in self._v1_addresses
        ]
        self._v2_contracts = [
            self._w3.eth.contract(address=a, abi=[_ORDER_FILLED_ABI_V2])
            for a in self._v2_addresses
        ]

    def iter_order_filled_events(
        self, from_block: int, to_block: int
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield decoded OrderFilled events in [from_block, to_block]."""
        for start in range(from_block, to_block + 1, self.BLOCK_CHUNK):
            end = min(start + self.BLOCK_CHUNK - 1, to_block)
            yield from self._fetch_chunk(start, end)

    def _fetch_chunk(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for contract, version in (
            *[(c, 1) for c in self._v1_contracts],
            *[(c, 2) for c in self._v2_contracts],
        ):
            try:
                logs = contract.events.OrderFilled.get_logs(
                    from_block=from_block, to_block=to_block
                )
            except Exception:
                logs = []

            for log in logs:
                block = self._w3.eth.get_block(log["blockNumber"])
                results.append(
                    {
                        "tx_hash": log["transactionHash"].hex(),
                        "log_index": log["logIndex"],
                        "block_ts": block["timestamp"],
                        "block_number": log["blockNumber"],
                        "contract_version": version,
                        "maker": log["args"]["maker"],
                        "taker": log["args"]["taker"],
                        "maker_asset_id": log["args"]["makerAssetId"],
                        "taker_asset_id": log["args"]["takerAssetId"],
                        "maker_amount": log["args"]["makerAmountFilled"],
                        "taker_amount": log["args"]["takerAmountFilled"],
                    }
                )

        return results

    def ts_to_block(self, ts: int) -> int:
        """Binary search for the block number closest to a given timestamp."""
        lo, hi = 1, self._w3.eth.block_number
        while lo < hi:
            mid = (lo + hi) // 2
            block_ts = self._w3.eth.get_block(mid)["timestamp"]
            if block_ts < ts:
                lo = mid + 1
            else:
                hi = mid
        return lo
