#!/usr/bin/env node
/**
 * Auto-redemption script for the Polymarket bot daemon.
 * Called by bot/redeemer.py with a condition_id argument.
 * 
 * Usage: node redeem_auto.mjs <condition_id>
 * 
 * Outputs "SUCCESS" on successful redemption.
 * Exit code 0 on success, 1 on failure.
 */
import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { polygon } from "viem/chains";
import { RelayClient, RelayerTxType } from "@polymarket/builder-relayer-client";
import { BuilderConfig } from "@polymarket/builder-signing-sdk";
import { ethers } from "ethers";

const CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";
const USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";
const REDEEM_ABI = [
  "function redeemPositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] indexSets)"
];

const PK = process.env.POLYMARKET_PRIVATE_KEY;
const BUILDER_KEY = process.env.POLYMARKET_BUILDER_API_KEY;
const BUILDER_SECRET = process.env.POLYMARKET_BUILDER_API_SECRET;
const BUILDER_PASS = process.env.POLYMARKET_BUILDER_PASSPHRASE;

if (!PK || !BUILDER_KEY || !BUILDER_SECRET || !BUILDER_PASS) {
  console.error("Missing required env vars");
  process.exit(1);
}

const conditionId = process.argv[2];
if (!conditionId) {
  console.error("Usage: node redeem_auto.mjs <condition_id>");
  process.exit(1);
}

async function main() {
  const account = privateKeyToAccount(PK);
  const wallet = createWalletClient({
    account,
    chain: polygon,
    transport: http("https://polygon-bor-rpc.publicnode.com"),
  });

  const builderConfig = new BuilderConfig({
    localBuilderCreds: {
      key: BUILDER_KEY,
      secret: BUILDER_SECRET,
      passphrase: BUILDER_PASS,
    },
  });

  const client = new RelayClient(
    "https://relayer-v2.polymarket.com",
    137,
    wallet,
    builderConfig,
    RelayerTxType.PROXY,
  );

  const iface = new ethers.Interface(REDEEM_ABI);
  const redeemData = iface.encodeFunctionData("redeemPositions", [
    USDC, ethers.ZeroHash, conditionId, [1, 2],
  ]);

  console.log(`Redeeming condition: ${conditionId}`);
  
  const resp = await client.execute(
    [{ to: CTF, data: redeemData }],
    `Auto-redeem ${conditionId}`,
  );

  const txId = resp.transactionID || resp.transactionId;
  console.log(`Transaction submitted: ${txId}`);

  const result = await client.pollUntilState(
    txId,
    ["STATE_MINED", "STATE_CONFIRMED"],
    "STATE_FAILED",
    20,
    3000,
  );

  if (result) {
    const txHash = result.transactionHash || "";
    console.log(`SUCCESS txHash=${txHash}`);
    process.exit(0);
  } else {
    console.error("Transaction failed or timed out");
    process.exit(1);
  }
}

main().catch(err => {
  console.error(`Error: ${err.message}`);
  process.exit(1);
});
