import fetch from "node-fetch";

async function test() {
  const url = "https://api.allorigins.win/raw?url=https%3A%2F%2Fapp.ethena.fi%2Fapi%2Fyields%2Fprotocol-and-staking-yield";
  const res = await fetch(url);
  console.log(res.status);
  console.log(await res.text());
}
test();
