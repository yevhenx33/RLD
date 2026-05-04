const { ethers } = require("ethers");
async function main() {
  const provider = new ethers.JsonRpcProvider("http://127.0.0.1:8545");
  const txHash = "0xac467c4196ad7260b223e3a1c2fe4d53e52a2596aa2681c774acb04716959b76";
  const trace = await provider.send("debug_traceTransaction", [txHash, { tracer: "callTracer" }]);
  console.log(JSON.stringify(trace, null, 2));
}
main().catch(console.error);
