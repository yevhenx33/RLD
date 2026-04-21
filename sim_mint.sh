#!/bin/bash
source /home/ubuntu/RLD/docker/.env
RPC="http://127.0.0.1:8545"

export ETH_RPC_URL=$RPC
export ETH_FROM=$(cast wallet address --private-key $USER_A_KEY)

DEPLOY=$(cat /home/ubuntu/RLD/docker/deployment.json)
MARKET_ID=$(echo "$DEPLOY" | jq -r .market_id)
BROKER_ADDR="0xfec5a0a8501bef27bd952368fcf313174d8cf661"

echo "Minting without depositing collateral first (deltaDebt = 50 * 1e6)"
cast call --private-key $USER_A_KEY $BROKER_ADDR "modifyPosition(bytes32,int256,int256)" $MARKET_ID 0 50000000
