const { ethers } = require("ethers");
async function main() {
  const provider = new ethers.JsonRpcProvider("http://localhost:8545");
  const colAddr = "0xC070A317F23E9A4e982e356485416251dd3Ed944";
  const wrapABI = ["function aToken() view returns (address)"];
  const aTokenABI = ["function UNDERLYING_ASSET_ADDRESS() view returns (address)"];
  const wrapper = new ethers.Contract(colAddr, wrapABI, provider);
  const aTokenAddr = await wrapper.aToken();
  const aToken = new ethers.Contract(aTokenAddr, aTokenABI, provider);
  const usdcAddr = await aToken.UNDERLYING_ASSET_ADDRESS();
  console.log("USDC ADDR:", usdcAddr);
}
main().catch(console.error);
