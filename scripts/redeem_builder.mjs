import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { polygon } from "viem/chains";
import { RelayClient, RelayerTxType } from "@polymarket/builder-relayer-client";
import { BuilderConfig } from "@polymarket/builder-signing-sdk";
import { ethers } from "ethers";

const PRIVATE_KEY = process.env.POLYMARKET_PRIVATE_KEY;
const BUILDER_KEY = process.env.POLYMARKET_BUILDER_API_KEY;
const BUILDER_SECRET = process.env.POLYMARKET_BUILDER_API_SECRET;
const BUILDER_PASSPHRASE = process.env.POLYMARKET_BUILDER_PASSPHRASE;

const CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";
const USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";
const OXY_CONDITION = "0x688da28c436f9f49ee08df3afbe1d0de138de7a828478cf50eb9cc013766bdb3";
const DASH_CONDITION = "0xe923673ba211c54d72ba450877492a9e39582897edb9e8bf48fd1b42db746561";

const REDEEM_ABI = ["function redeemPositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] indexSets)"];

async function main() {
  const account = privateKeyToAccount(PRIVATE_KEY);
  const wallet = createWalletClient({
    account,
    chain: polygon,
    transport: http("https://polygon-rpc.com"),
  });
  
  console.log(`EOA: ${account.address}`);

  const builderConfig = new BuilderConfig({
    localBuilderCreds: {
      key: BUILDER_KEY,
      secret: BUILDER_SECRET,
      passphrase: BUILDER_PASSPHRASE,
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
  
  const oxyData = iface.encodeFunctionData("redeemPositions", [
    USDC, ethers.ZeroHash, OXY_CONDITION, [1, 2]
  ]);
  
  const dashData = iface.encodeFunctionData("redeemPositions", [
    USDC, ethers.ZeroHash, DASH_CONDITION, [1, 2]
  ]);

  console.log("\n📤 Submitting OXY + DASH redemption...");
  try {
    const resp = await client.execute([
      { to: CTF, data: oxyData, value: "0" },
      { to: CTF, data: dashData, value: "0" },
    ], "Redeem OXY+DASH positions");
    
    console.log(`TX ID: ${resp.transactionId}`);
    console.log("Waiting for confirmation...");
    const result = await resp.wait();
    console.log(`Result:`, JSON.stringify(result, null, 2));
  } catch (e) {
    console.error(`Error: ${e.message}`);
    if (e.response?.data) console.error(`Response:`, e.response.data);
    
    // Try individual OXY
    console.log("\n📤 Trying OXY only...");
    try {
      const resp2 = await client.execute([
        { to: CTF, data: oxyData, value: "0" },
      ], "Redeem OXY position");
      console.log(`TX ID: ${resp2.transactionId}`);
      const result2 = await resp2.wait();
      console.log(`OXY Result:`, JSON.stringify(result2, null, 2));
    } catch (e2) {
      console.error(`OXY error: ${e2.message}`);
      if (e2.response?.data) console.error(`Response:`, e2.response.data);
    }
  }
}

main().catch(console.error);
