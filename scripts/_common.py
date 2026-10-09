"""Shared helpers for the GitEscrow Studio Next scripts.

Keys: a throwaway key per role is generated on first use and stored in
deployments/.keys/ (git-ignored). Override with GITESCROW_<ROLE>_KEY. These are
Studio Next test accounts funded from the network's faucet RPC - never put a
real key in here.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any

from genlayer_py import create_account, create_client, generate_private_key
from genlayer_py.chains import studio_devnet

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "contracts" / "git_escrow.py"
DEPLOYMENT_FILE = ROOT / "deployments" / "studio-next.json"
KEY_DIR = ROOT / "deployments" / ".keys"

RPC_URL = os.environ.get("GITESCROW_RPC_URL", "https://studio-next.genlayer.com/api")
EXPLORER_URL = "https://explorer-studio-next.genlayer.com"
ATTO = 10**18
SIGNING_CHAIN_ID = 61997  # Studio Next; passed to the contract constructor and bound into every signature


def chain():
    """Studio Next serves chain 61997; the bundled preset only names a different host."""
    return dataclasses.replace(studio_devnet, rpc_urls={"default": {"http": [RPC_URL]}})


def account(role: str):
    env = os.environ.get(f"GITESCROW_{role.upper()}_KEY")
    if env:
        return create_account(env)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    path = KEY_DIR / f"{role}.key"
    if not path.exists():
        key = generate_private_key()
        path.write_text(key if isinstance(key, str) else "0x" + bytes(key).hex())
        path.chmod(0o600)
    return create_account(path.read_text().strip())


def client(role: str):
    return create_client(chain=chain(), account=account(role))


def retry(fn, attempts: int = 4, wait: float = 6.0):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - studio drops connections now and then
            last = exc
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"RPC failed after {attempts} attempts: {last}")


def balance(c, address: str) -> int:
    resp = retry(lambda: c.provider.make_request("eth_getBalance", [address, "latest"]))
    return int(resp.get("result", "0x0"), 16)


def ensure_funds(c, address: str, target: int = 5_000 * ATTO) -> None:
    have = balance(c, address)
    if have < target:
        retry(lambda: c.fund_account(address, target - have + 1))


def is_success(receipt: Any) -> bool:
    """A decided transaction is only a success if consensus agreed AND execution returned."""
    get = receipt.get if isinstance(receipt, dict) else (lambda k, d=None: getattr(receipt, k, d))
    agreed = str(get("result_name", "")) in ("AGREE", "MAJORITY_AGREE")
    executed = str(get("txExecutionResultName", "FINISHED_WITH_RETURN")) == "FINISHED_WITH_RETURN"
    return agreed and executed


LAST_TX = {"hash": ""}  # hash of the most recent transaction sent, kept even when it is rejected


def send(c, address: str, fn: str, args: list, value: int = 0, label: str | None = None) -> dict:
    """Write through consensus, wait for the decision, fail loudly on a bad status."""
    fees = retry(lambda: c.estimate_transaction_fees())
    tx_hash = retry(lambda: c.write_contract(address=address, function_name=fn, args=args, value=value, fees=fees))
    LAST_TX["hash"] = tx_hash if isinstance(tx_hash, str) else "0x" + bytes(tx_hash).hex().removeprefix("0x")
    receipt = c.wait_for_transaction_receipt(transaction_hash=tx_hash, wait_until="decided", retries=200, interval=3000)
    ok = is_success(receipt)
    print(f"  {'OK ' if ok else 'ERR'} {label or fn}  tx={tx_hash if isinstance(tx_hash, str) else tx_hash.hex()}")
    if not ok:
        raise RuntimeError(f"{fn} was not accepted by consensus: {receipt}")
    return receipt


def view(c, address: str, fn: str, args: list | None = None):
    return retry(lambda: c.read_contract(address=address, function_name=fn, args=args or []))


def contract_address_from(receipt: Any) -> str | None:
    def dig(obj: Any, *path: str):
        for key in path:
            obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if obj is None:
                return None
        return obj

    for path in (("tx_data_decoded", "contract_address"), ("txDataDecoded", "contractAddress"),
                 ("data", "contract_address"), ("contract_address",), ("to_address",), ("recipient",)):
        found = dig(receipt, *path)
        if isinstance(found, str) and found.startswith("0x") and len(found) == 42:
            return found
    return None


def load_deployment() -> dict:
    if not DEPLOYMENT_FILE.exists():
        raise SystemExit("No deployment recorded. Run: python scripts/deploy.py")
    return json.loads(DEPLOYMENT_FILE.read_text())


def now() -> int:
    return int(time.time())


def authorization_message(contract: str, action: str, milestone_id: int, commit_sha: str, delivery_ref: str,
                          nonce: int, expires_at: int, chain_id: int = SIGNING_CHAIN_ID) -> str:
    """The exact text the contract asks a wallet to sign (see authorization_message in git_escrow.py)."""
    return "\n".join([
        "GitEscrow authorization v1",
        f"action: {action}",
        f"chain: {chain_id}",
        f"contract: {contract.lower()}",
        f"milestone: {milestone_id}",
        f"commit: {commit_sha.strip().lower()}",
        f"ref: {delivery_ref.strip()}",
        f"nonce: {nonce}",
        f"expires: {expires_at}",
    ])


def sign_authorization(role: str, c, contract: str, action: str, milestone_id: int, commit_sha: str,
                       delivery_ref: str = "", ttl: int = 3600) -> tuple[int, int, str]:
    """Sign an authorization with the role's key (EIP-191 personal_sign, like an injected wallet would).

    Returns (nonce, expires_at, signature) ready for evaluate_milestone_delivery / approve_milestone."""
    from eth_account.messages import encode_defunct

    acct = account(role)
    nonce = int(view(c, contract, "get_nonce", [acct.address]))
    expires_at = now() + ttl
    text = authorization_message(contract, action, milestone_id, commit_sha, delivery_ref, nonce, expires_at)
    signed = acct.sign_message(encode_defunct(text=text))
    return nonce, expires_at, "0x" + bytes(signed.signature).hex()


def github_head(repo: str, branch: str) -> str:
    """Current head commit of a public branch (unauthenticated GitHub API)."""
    import urllib.request

    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{branch}",
                                 headers={"User-Agent": "GitEscrow-scripts", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["sha"]
